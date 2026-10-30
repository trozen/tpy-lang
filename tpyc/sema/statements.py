"""
TurboPython Statement Analysis

Statement analysis including variable declarations, assignments, and control flow.
"""

from __future__ import annotations
import enum
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterator, Mapping

from ..holders import Holder, IterHolder
from ..typesys import (drop_own_marker, pointer_repr_optional, pointer_variant_union,
                       ResolvedBinop, store_slot_type, store_takes_elements, answers_as_call,
                       BASIC_SLICE, SLICE)
from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, OwnType, ReadonlyType, make_readonly,
    is_indirection_type,
    FinalType,
    PendingListType, PendingDictType, PendingContainerType, make_list, PendingNumType, PendingSetType, PendingStrType, PendingBytesType, PendingViewType, NominalType, TypeParamRef,
    ListLiteralInfo, ContainerLiteralInfo, ViewVarInfo, PtrType, is_readonly_ptr, NoneType, OptionalType, AnyType, UnionType, UnknownElementType,
    is_c_abi_allowed, is_c_abi_element_allowed, c_abi_type_hint,
    C_ABI_TYPE_ERROR,
    is_void_like_type,
    unwrap_readonly, unwrap_own, unwrap_qualifiers, is_any_str_type, is_any_bytes_type, TupleType, own_tuple_target,
    declared_result_readonly,
    RecursiveAliasInstanceType,
    collapse_tuple_own_elements,
    global_binds_by_reference, type_contains_own,
    LiteralType,
    ViewTypeFamily, view_family_for_type, VIEW_TYPE_FAMILIES,
    PendingGenericInstanceType, contains_fn_type,
    INT32, VOID, BIGINT, FLOAT, STRVIEW, BYTES, BYTESVIEW, BOOL, is_protocol_type, is_protocol_union, final_type_str_to_strview,
    is_final_allowed_inner, FINAL_INNER_TYPE_ERROR, is_constexpr_eligible,
    qualify_exception_name, is_return_exception, is_exception_record, error_return_matches,
    record_owner,
    FunctionInfo, ParamInfo, RecordInfo,
    make_ref, unwrap_ref_type, RefType, param_has_mutable_borrow_surface,
    is_integer_type, is_any_int_type, is_numeric_type, is_primitive_type, is_readonly_span,
    is_bufferless_scalar, unwrap_send_sync, unwrap_optional_own, owned_payload,
    is_float_type, is_any_float_type, is_polymorphic_subclass_fact,
    resolve_int_literals,
    yield_uses_borrow_slot, yield_always_borrows, GenExprType,
    is_dyn_protocol, is_fn_type, coro_struct_owner,
    ConcreteCoroType, ConcreteFrameType, ConcreteGenType, make_concrete_coro,
    make_cancellable)
from ..parse import (
    collect_name_refs,
    walk_body_stmts,
    TpyExpr,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyDelAttr, TpyExprStmt, TpyReturn, TpyYield,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyAssert,
    TpyRaise, TpyExceptHandler, TpyTry, TpyWith, TryTier,
    TpyGlobal, TpyNonlocal, TpyNestedDef,
    TpyCall, TpyMethodCall, TpyArrayLiteral, TpyListComprehension, TpyCoerce,
    TpyListRepeat, TpyDictLiteral, TpySetLiteral,
    TpySubscript, SubscriptDunder, TpySlice, TpyStrLiteral, TpyName, TpyTupleLiteral,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyUnaryOp,
    TpyNoneLiteral,
    TpyFieldAccess, TpyFunction, TupleElemCapture,
    TpyMatch, TpyBinOp, TpyIfExpr,
    TpyLambda, TpyNamedExpr, TpyGeneratorExpression, TpyDictComprehension,
    TpySetComprehension,
    is_stable_address_lvalue,
)
from ..coercions import (Coercion, CoercionContext, int_payload,
                         peel_identity_coercions)
from ..namespace import BindingKind, NameBinding
from ..symbol_binding import SymbolKind, lookup_imported
from ..prescan import (
    bound_names_of, walrus_names_of, scope_bound_names, ScanResult,
    scan_reassigned_vars, collect_in_place_writes,
    storage_spelling,
    FactKills, liveness_alias_sources,
    collect_nonlocals, closure_exports,
)
from ..liveness import (analyze_last_uses, closure_pinned_names,
                        collect_finally_return_candidates,
                        nested_def_free_names,
                        collect_deleted_names, collect_finally_rebound_returns,
                        collect_nested_def_nonlocal_rebinds,
                        stmts_terminate, tuple_literal_leaves,
                        while_head_always_true)
from ..parse.nodes import (
    SourceLocation, VarLinkage, op_spelling, name_path,
)
from .arg_binding import source_view
from .calls import class_callee
from .scope_tracker import register_binder, unregister_binder
from .call_lends import (
    MayLend, ViewLend, borrowing_ctor_arg, call_callee, frame_operand_keys,
    path_holds_root,
)
from .context import (
    CallOperands, OwnSlot, PendingLocal, expr_yields_non_null_ptr, LoopClauseEdges,
    record_borrow_binding, record_stmt_borrow_binding,
    holds_generator_object, holds_frame_object, note_owned_local,
    note_nonowning_bind, ConsumingLoop, FinallyCapture, SinkCopy, UnpackClaim,
    frame_binding_calls, frame_binding_fact, record_frame_binding_roots,
    NestedMutationMark,
)
from .storage_walk import (EscapePosition, LoanSource, MarkRequest, Use,
                           borrow_key, call_loans, holder_root, mark_roots,
                           path_key, receiver_leaves,
                           storage_key, storage_roots, StorageKey,
                           view_leaves)
from .flow_facts import FlowFacts, condition_walrus_targets
from .literal_utils import is_char_literal_init
from ..diagnostics import SemanticError, NOCOPY_REMEDIATION_HINT, not_assigned_message
from .match import MatchAnalyzer
from .narrowing import NarrowingTracker
from .scope_tracker import ScopeTracker
from .init_tracker import InitTracker
from .receiver_calls import (
    check_implicit_readonly_receiver, credit_implicit_receiver_call,
    implicit_readonly_result_hint,
    record_implicit_call, record_receiver_edges,
)
from .aug_hold import aug_hold_parts
from .loop_frames import decide_copies_var, for_live_region, worth_const_ref
from .binding_ownership import step_handover_paths
from .iter_loans import (
    IterElementSource, iter_element_source, record_iteration_pull,
    iteration_yields_copies,
    _record_iter_receiver_mutation, check_iter_receiver_loans,
    frame_capture_loans, hold_whole,
    is_dangling_temporary_arg,
    iterator_advanced_twice, register_iteration_loans, temp_arg_kept_alive,
    temp_arg_spelling,
)
from .dyn_erasure import erased_source
from .own_copy import (contains_reference_type, copy_result_under_write,
                       lost_write_message,
                       type_has_type_param, warn_value_call_binding)
from .value_range import ValueRange
if TYPE_CHECKING:
    from .context import SemanticContext, BorrowTracker
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .list_literals import IterableHelper
    from .expressions import ExpressionAnalyzer
    from .protocols import ProtocolChecker
    from ..diagnostics import Scope

from .capture_copies import warn_closure_captures
from .frame_traits import frame_type_of_function
from .alias_rebind import (binding_roots, decide_rebind_storage,
                           finalize_pointer_tuples, is_reference_local,
                           stamp_bind_kind, stamp_unpack_rebinds,
                           unpack_target_records)
from .storage_policy import first_binding_storage
from .numeric_lattice import same_width_family
from .type_join import (expression_spelling, operand_spelling,
                        python_type_name, user_type_name,
                        wider_store_message)
from .compatibility import (TupleSink, inferred_field_slot,
                            owned_tuple_slots, result_owns_storage)
from .context import (
    BorrowKind, EphemeralKind, INVALIDATING_BORROW_KINDS,
    CLASS_CONSTANT_CONTEXT, PENDING_CONTAINER_TYPES, element_index_key,
    element_loan_mutation_warning, storage_loan_mutation_warning,
    register_binding_borrow, ephemeral_borrow_root, contains_pending_leaf,
    SitedKey,
)
from ..value_category import (
    is_rvalue_source, call_returns_cpp_ref, async_result_aliases,
    async_return_form, AsyncReturnForm, iterator_source_callee,
    peel_value_wrappers, tuple_literal_elems, peel_coerce,
    binds_fresh_call_value, binds_owned_value, consuming_iter_of,
    holds_no_pointer, value_may_point,
)
from .expressions import (_nested_def_free_names, _find_list_member,
                          _names_rebound_by, generic_constructor_factory,
                          reject_rebound_iterated_capture)
from .pending_num import (ContainerCells, Entry, PendingNums, PendingNumCell,
                          at_path, value_family, is_numeric_slot,
                          is_pending_num, literal_entries, pairs_as,
                          value_leaves, pending_list_of, root_of, strip_int,
                          with_container, with_list_elem)
from .list_elem import container_call
from .nonlocal_binding import (bind_nonlocals, enclosing_scope_facts,
                               enter_enclosing_scope, nonvalue_params)
from .slot_hint import SlotHint
from .local_deduction import (
    collect_pending_source_types, mark_pending_list_mutated,
    note_elem_binding, note_pending_elem_read,
    temporary_view_bind_message,
    view_slot_source_is_temporary,
    view_source_is_temporary,
)
from tpyc import modules as builtin_modules
from tpyc import qnames
from ..type_def_registry import (
    is_iterator_adapter,
    is_span, is_list,
    is_char_type, is_str_type, is_string_type, is_str_view_type,
    is_bytes_type, is_bytearray_type, is_bytes_view_type,
    is_borrowing_view_type,
    is_fixed_int_type, is_big_int_type,
    protocol_info_of,
)


# Replaces a recorded type by a function of its value at that point.
_Rewrite = Callable[[Callable[[TpyType], TpyType]], None]

# How a diagnostic names the compound statement a nested `def` sits in.
_BLOCK_KEYWORD = {
    TpyIf: "if",
    TpyWhile: "while",
    TpyForEach: "for",
    TpyTry: "try",
    TpyWith: "with",
    TpyMatch: "match",
}


def _converts(value: TpyExpr, written: TpyExpr) -> bool:
    """Whether the coercions `coerce_expr` recorded between the written
    return value and the slot convert it -- an identity str-family coercion
    (`String` into `str`: both spell std::string) does not."""
    return peel_identity_coercions(value, written) is not written


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


def own_consume_key(ctx: SemanticContext, name: str) -> OwnSlot | None:
    """The never-consumed credit a move out of `name` carries: the param
    itself, or what the local `name` took from one."""
    if name in ctx.func.current_param_names:
        return OwnSlot(name)
    return ctx.func.own_consume_aliases.get(name)


class BindingOwns(enum.Enum):
    """What a binding of a reference-type local holds (`record_name_binding`):
    an object its source created, the object a name source handed over at
    its last use, or a borrow."""
    FRESH = "fresh"
    MOVED = "moved"
    BORROW = "borrow"


def note_binding_loans(ctx: SemanticContext, name: str,
                       var_type: TpyType | None, init: TpyExpr) -> None:
    """What a binding of `name` to `init` borrows, and the copy a value
    call's binding states, alike for a declaration and a walrus.

    Views are value types in C++ but still carry a reference into foreign
    storage, so a view local borrows its source exactly like a non-value
    local does -- `v = a.strip()` registers the receiver borrow (the
    temp-receiver warning, the mutate-while-borrowed check on a later
    `a += ...`). An INFERRED str/bytes local registers no loan: every write
    to what it views is a source-storage event the deduction answers by
    owning the local. A spelled `StrView` / `BytesView` cannot fall back,
    and keeps the loan and its mutate-while-borrowed diagnostic."""
    borrow_storage = (None if isinstance(unwrap_readonly(var_type),
                                         PendingViewType)
                      else var_type)
    bare_init = init.expr if isinstance(init, TpyCoerce) else init
    if (borrow_storage is not None and borrow_storage.is_value_type()
            and type_has_type_param(borrow_storage)):
        # An open `T` local of a generic body: whether binding a fresh
        # result copies a reference is the instantiation's question, so
        # the binding states the hedged copy contract.
        warn_value_call_binding(ctx, bare_init, f"local '{name}'")
    if var_type is not None:
        register_binding_loans(ctx, name, init, var_type)
    if (borrow_storage is not None
            and (not borrow_storage.is_value_type()
                 or is_borrowing_view_type(unwrap_readonly(borrow_storage)))):
        warn_value_call_binding(ctx, bare_init, f"local '{name}'")


def record_name_binding(an: 'StatementAnalyzer | ExpressionAnalyzer',
                        name: str, var_type: TpyType | None, init: TpyExpr,
                        *, fresh: bool,
                        init_type: TpyType | None) -> BindingOwns:
    """The binding record of `name = init` (`fresh`: `name` binds for the
    first time), alike for every binding of a name -- a declaration, an
    assignment, a walrus: hoist eligibility (`rvalue_vars`), the
    move-through of a name source at its last use, the ownership record
    (`record_binding_ownership`), the fresh-ctor fact, the loans and the
    provenance."""
    owns = _classify_name_binding(an, name, var_type, init, fresh=fresh,
                                  init_type=init_type)
    note_binding_loans(an.ctx, name, var_type, init)
    note_binding_provenance(an, name, var_type, init)
    return owns


def _classify_name_binding(an: 'StatementAnalyzer | ExpressionAnalyzer',
                           name: str, var_type: TpyType | None,
                           init: TpyExpr, *, fresh: bool,
                           init_type: TpyType | None) -> BindingOwns:
    """`record_name_binding`'s ownership half."""
    ctx = an.ctx
    ctx.func.own_consume_aliases.pop(name, None)
    if not an.compat.is_lvalue(init):
        ctx.func.rvalue_vars.add(name)
        if not record_binding_ownership(ctx, name, var_type, init,
                                        lvalue=False):
            return BindingOwns.BORROW
        # Fresh-ctor local: a non-reassigned local whose sole binding is a
        # constructor call of its exact static type. Its dynamic type is
        # then provably its static type, so the polymorphic-slicing guard
        # may move it into an owned poly slot (Box[P]) like a fresh-rvalue
        # ctor at the store. current_reassigned_vars comes from a
        # whole-function prescan (all branches), so ANY later rebind
        # excludes the name here; a name that could hold a subclass value
        # on some path was never added. var_type == init_type excludes a
        # widening annotation.
        if (name not in ctx.func.current_reassigned_vars
                and isinstance(init, TpyCall)
                and isinstance(init.func, TpyName)
                and ctx.registry.get_record(init.func.name) is not None
                and init_type is not None and var_type == init_type):
            ctx.func.current_fresh_ctor_locals.add(name)
        else:
            ctx.func.current_fresh_ctor_locals.discard(name)
        return BindingOwns.FRESH
    # Move-through: an alias at the last use of its source takes the
    # object over. Both target and source must be non-reassigned locals
    # (reassigned ones become pointer locals in codegen).
    hands_over = (var_type is not None
                  and not var_type.is_value_type()
                  and pointer_repr_optional(var_type) is None
                  and not is_protocol_union(var_type)
                  and not holds_generator_object(var_type))
    reassigned = ctx.func.current_reassigned_vars
    plain_bind = (isinstance(init, TpyName) and fresh
                  and name not in reassigned and init.name not in reassigned)
    if plain_bind and not hands_over:
        # A value moves through with no owning slot to record.
        an.compat.retract_hidden_borrowed_last_use(init)
    if (plain_bind and hands_over
            and an.compat.claim_name_move(init, OwnType(var_type))):
        ctx.func.rvalue_vars.add(name)
        note_owned_local(ctx, name, var_type)
        ctx.func.move_through_vars.add(name)
        # The source moved into this local, so consuming the local consumes
        # what the source's Own param handed in.
        key = own_consume_key(ctx, init.name)
        if key is not None:
            ctx.func.own_consume_aliases[name] = key
        return BindingOwns.MOVED
    ctx.func.rvalue_vars.discard(name)
    record_binding_ownership(ctx, name, var_type, init, lvalue=True)
    return BindingOwns.BORROW


def record_binding_ownership(ctx: SemanticContext, name: str,
                             var_type: TpyType | None, init: TpyExpr, *,
                             lvalue: bool) -> bool:
    """Whether a binding of `name` owns what it binds, recorded alike by
    every binding form (a declaration, an assignment, a walrus): owned only
    when the source genuinely creates a value (a constructor, an `Own`
    return, a literal, an operator), never for an lvalue or a
    borrow-producing call (a `T&` or pointer-form result), whose later
    move-out would corrupt the aliased source. A borrow records the
    statement-level borrow fact. Move-through is the declaration's own."""
    if not lvalue and binds_owned_value(ctx, init):
        note_owned_local(ctx, name, var_type)
        return True
    ctx.func.owned_locals.discard(name)
    record_stmt_borrow_binding(ctx, name, var_type, init)
    return False


def note_binding_provenance(an: 'StatementAnalyzer | ExpressionAnalyzer',
                            name: str, var_type: TpyType | None,
                            init: TpyExpr) -> None:
    """Where the value a binding of `name` holds comes from -- whether it
    derives from a parameter, may be returned, and (a `Ptr`) is non-null --
    recorded alike by every binding form: a declaration, an assignment, a
    walrus. `an` is the analyzer doing the binding."""
    if var_type is None:
        return
    if _needs_provenance_tracking(var_type):
        derived, safe = an.compat.binding_durability(init)
        an.init.mark_provenance(name, derived)
        an.init.mark_safe_to_return(name, safe)
    if isinstance(var_type, PtrType):
        an.init.mark_non_null_ptr(name, expr_yields_non_null_ptr(
            init, an.ctx.func.non_null_ptr_vars))


def _register_call_result_borrow(ctx: SemanticContext, borrower: Holder,
                                 expr: TpyExpr) -> None:
    """File the loans a binding of a call's result takes (`call_loans`
    under MAY): through each call the value is, or is a field / element /
    select arm of, on each operand its result may point into. A guess about
    a pending callee is an OPAQUE loan -- it gates a later move of the
    source, nothing more; a receiver lent only by declaration is a FIELD
    loan (its shape is unknown: a write through the result climbs it,
    nothing invalidates it); a result kept whole is an ALIAS hold. A
    temporary the result may point into is warned: it dies with the
    statement."""
    bt = ctx.func.borrow_tracker
    warned: list[tuple[TpyExpr, int]] = []

    def warn_once(loan: LoanSource, text: str) -> None:
        if not any(c is loan.call and i == loan.source.idx for c, i in warned):
            warned.append((loan.call, loan.source.idx))
            ctx.warning(text, loan.call)

    for loan in call_loans(expr, MayLend(ctx)):
        fi = call_callee(loan.call)
        src = loan.source
        key = loan.key if isinstance(loan.key, StorageKey) else None
        if loan.temp_backed:
            # An owned tuple element copied into the argument temporary: the
            # result points into storage that dies as a scalar temporary
            # argument's does, and borrows nothing of the caller's.
            if fi is not None and not temp_arg_kept_alive(
                    fi, src.idx, src.expr, src.slot, ctx):
                warn_once(loan, _temp_arg_warning(fi, src.idx))
            continue
        # Whether a write through the result writes the loan's storage:
        # not through a receiver the call grants no write through (the
        # policy's `grants_write`: a readonly getter, a readonly method),
        # else from the lent operand as spelled for a member key.
        holds = (False if src.idx == -1 and not src.grants_write
                 else path_holds_root(ctx.registry, loan.operand,
                                      ctx.get_expr_type)
                 if key is not None and key.members else None)
        if loan.assumed:
            if key is not None and key.as_name() != borrower:
                bt.add_borrow(key, borrower, BorrowKind.OPAQUE,
                              holds_root=holds)
            continue
        if key is None:
            if fi is None or not (loan.fresh and is_dangling_temporary_arg(
                    loan.operand)):
                continue
            if src.idx >= 0:
                if not temp_arg_kept_alive(fi, src.idx, src.expr, src.slot,
                                           ctx):
                    warn_once(loan, _temp_arg_warning(fi, src.idx))
            elif src.idx == -1 and not loan.declared_only:
                # A borrow-returning callee can hand back storage that
                # OUTLIVES its receiver (a global, a longer-lived object), so
                # the receiver dying at end-of-statement is never a reason to
                # call the result unborrowed -- the provenance is the whole
                # question here. A receiver lent only by declaration is no
                # evidence the other way: the body reached the result some
                # other way (through a `Ptr`, a global).
                warn_once(loan, "Result borrows from temporary receiver "
                          "object; the temporary is destroyed at "
                          "end-of-statement")
            continue
        if loan.held_whole and src.idx >= 0:
            hold_whole(bt, borrower, [key])
            continue
        # An iterator-returning callee hands back a HANDLE into its sources (a
        # combinator object, a generator frame): advancing it touches nothing
        # it points at, which is what the ITER kind tells the mutation climb.
        bt.add_borrow(key, borrower, BorrowKind.FIELD if loan.declared_only
                      else BorrowKind.ITER if iterator_source_callee(fi)
                      else BorrowKind.ELEMENT, holds_root=holds)
    for leaf in receiver_leaves(expr):
        for key, info in frame_capture_loans(peel_value_wrappers(leaf)):
            bt.add_borrow(key, borrower, info.kind, on_element=info.on_element,
                          elem_index=info.elem_index, merge=True)


def _temp_arg_warning(fi: FunctionInfo, idx: int) -> str:
    return (f"Result borrows from temporary {temp_arg_spelling(fi, idx)}; "
            f"the temporary is destroyed at end-of-statement")


def _register_tuple_binding_borrows(
        ctx: SemanticContext, borrower: Holder, value: TpyExpr,
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
                _register_tuple_binding_borrows(
                    ctx, borrower, leaf.elem, slot)
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
            register_binding_loans(ctx, borrower, leaf.elem, leaf.slot)
    elif isinstance(inner, TpyIfExpr):
        _register_tuple_binding_borrows(ctx, borrower, inner.then_expr, bare)
        _register_tuple_binding_borrows(ctx, borrower, inner.else_expr, bare)
    else:
        if isinstance(inner, (TpyName, TpySubscript, TpyFieldAccess)):
            register_binding_borrow(ctx, borrower, inner)
        _register_call_result_borrow(ctx, borrower, inner)


def register_binding_loans(ctx: SemanticContext, holder: Holder,
                           value: TpyExpr,
                           bound_type: 'TpyType | None') -> None:
    """The loans a binding of `value` at type `bound_type` files for
    `holder` -- a local, a walrus target, a tuple leaf's binding: the
    tuple's borrowed elements, the place a reference value aliases (whole
    object ALIAS, field FIELD, element ELEMENT), and the operands a call
    result may point into (a borrowing view's too). An inferred str/bytes view (`PendingViewType`)
    files no call-result loan: every write to what it views owns it."""
    if bound_type is None:
        return
    _register_tuple_binding_borrows(ctx, holder, value, bound_type)
    if not bound_type.is_value_type():
        register_binding_borrow(ctx, holder, value)
    bare = unwrap_readonly(bound_type)
    if isinstance(bare, PendingViewType):
        return
    if not bound_type.is_value_type() or is_borrowing_view_type(bare):
        _register_call_result_borrow(ctx, holder, peel_coerce(value))


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
    language calls a tuple a value type. A container whose literal has not
    settled yet is named as the Python container it is."""
    bare = unwrap_readonly(t)
    what = ("a tuple of reference types" if isinstance(bare, TupleType)
            else "non-value type")
    return (f"Cannot reassign global variable '{name}' of {what} "
            f"'{user_type_name(collapse_tuple_own_elements(bare))}'")


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


def _stored_tuple_target(target_type: TpyType) -> TupleType | None:
    """`own_tuple_target` of an assignment target's type. A cell
    container's part reads as a reference to its storage, members
    included; the tuple stored there is the part itself."""
    target = own_tuple_target(unwrap_ref_type(target_type))
    if target is None or not any(isinstance(e, RefType)
                                 for e in target.element_types):
        return target
    return TupleType(tuple(unwrap_ref_type(e) for e in target.element_types))


def _slot_owns(t: TpyType) -> bool:
    """A return slot that takes ownership of (some of) its value: `Own[T]`,
    or an Optional / union / tuple with an `Own` part."""
    t = unwrap_readonly(unwrap_ref_type(t))
    if isinstance(t, OwnType):
        return True
    if isinstance(t, (OptionalType, UnionType)):
        return any(_slot_owns(m) for m in t.inner_types())
    return own_tuple_target(t) is not None


def _holds_reference(t: TpyType) -> bool:
    """Whether a value of `t` holds a reference-type object by value, whose
    copy CPython would not make."""
    t = unwrap_readonly(unwrap_own(unwrap_ref_type(t)))
    if isinstance(t, TupleType):
        return any(_holds_reference(e) for e in t.element_types)
    if isinstance(t, (OptionalType, UnionType)):
        return any(_holds_reference(m) for m in t.inner_types())
    return not t.is_value_type()


def _local_points_into_source(t: TpyType) -> bool:
    """Whether a local of type `t` bound from storage points into it: a
    reference type (`T* p = &(src)`), or a tuple -- bare or Optional -- with
    a pointer-repr element, whose borrow form holds its elements' addresses
    (lent element by element). A rebound one marks its source's roots
    mutated, so its pointers are never const; one bound off an ephemeral
    borrow is that borrow under another name."""
    if not t.is_value_type():
        return True
    bare = unwrap_readonly(unwrap_ref_type(t))
    return ((isinstance(bare, TupleType) and bare.has_pointer_repr_element())
            or (isinstance(bare, OptionalType)
                and bare.wraps_pointer_repr_tuple()))


def update_ephemeral_alias_fact(ctx: SemanticContext, name: str,
                                var_type: 'TpyType | None',
                                init_expr: 'TpyExpr | None') -> None:
    """Propagate (or clear) the ephemeral-borrow fact through an aliasing
    binding of `name`, alike for every binding of a name: `q = p` (or
    `q = p.field` for a non-value field) of an ephemeral loop var is the
    same stale-slot borrow under another name, so escapes through the
    alias must reject like the direct form. Value bindings COPY and carry
    no fact; any other rebind clears it.

    The alias inherits the root's KIND, so a GATE_ONLY hold reaches the
    slot verdict through an alias exactly as the direct form does.
    """
    eph = ctx.func.ephemeral_borrow_vars
    root = None
    if init_expr is not None and var_type is not None:
        if _local_points_into_source(var_type):
            root = ephemeral_borrow_root(eph, init_expr)
    if root is not None:
        eph[name] = eph[root]
    else:
        eph.pop(name, None)


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
        # Set after construction, from the compatibility layer it wraps.
        self.pend: PendingNums
        expr.describe_statement_use = self._describe_statement_use

    def _describe_statement_use(self, expr: TpyExpr | None = None) -> str:
        """What the statement under analysis uses `expr`, an expression it
        reads directly, as, for a diagnostic."""
        stmt = self._stmt_stack[-1] if self._stmt_stack else None
        if isinstance(stmt, TpyMatch):
            return "a match subject"
        if isinstance(stmt, (TpyIf, TpyWhile)):
            return "a condition"
        if isinstance(stmt, TpyForEach):
            return "a loop iterable"
        if isinstance(stmt, TpyWith):
            return "a 'with' item"
        if isinstance(stmt, TpyDelItem):
            return "a 'del' key"
        # Stores into a local or a numeric field take a pending value; what
        # needs the type is a store into an element or a key.
        if isinstance(stmt, TpyAugAssign):
            if expr is not None and expr is stmt.value:
                return f"the operand of '{op_spelling(stmt.op)}='"
            if isinstance(stmt.target, TpyName):
                return f"the target of '{op_spelling(stmt.op)}='"
            return "the value of an augmented assignment to an element"
        if isinstance(stmt, TpyAssign):
            target = storage_spelling(stmt.target)
            if target is None and isinstance(stmt.target, TpySubscript):
                obj = storage_spelling(stmt.target.obj)
                target = f"{obj}[...]" if obj is not None else None
            return (f"the value stored into '{target}'" if target is not None
                    else "a value stored into an element")
        if isinstance(stmt, TpyReturn):
            return "a returned value"
        if isinstance(stmt, TpyYield):
            return "a yielded value"
        if isinstance(stmt, TpyVarDecl):
            return f"the value bound to '{stmt.name}'"
        return "this use"

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
            iterator = (IterHolder(stmt) if isinstance(stmt, TpyForEach)
                        else None)
            with self.scopes.loop_scope(stmt.body, iterator) as inner_scope:
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
            exit_from_head: bool = False,
            pre_loop: 'FlowFacts') -> None:
        """Close a loop: exit facts, both clauses' bindings, the break join.

        Every loop arm ends here, so the order the clauses are joined in is
        stated once. A name first bound in a clause counts as assigned after
        the loop only when the clause provably ran (`runs_once`) and the
        binding is on every edge that leaves it: the body's own edges,
        already joined into `edges.body_assigned`; the `else` clause, which
        the fall-through path ran by definition; and every `break`, which
        leaves past the rest of the body AND past the `else`. A
        `return`/`raise` never joins here at all.

        `before` is what the loop is left with through its head: a `for`'s
        pre-loop state (joined with the body's end), a `while` head's FALSE
        path (`exit_from_head`), which the `else` clause runs on; each
        `break` then joins with its own state. `elem_type` is a `for`'s, whose loop variable is a binding of the
        same kind; an `async for` has no `else` clause to walk (the parser
        rejects one).
        """
        self.init.apply_loop_exit_facts(before, runs_once=runs_once,
                                        exit_from_head=exit_from_head)
        if isinstance(stmt, TpyForEach) and stmt.iterable.pull_runs_code:
            # The loop leaves after a last step, which is a call.
            self.narrowing.invalidate_for_code_run()
        # Loop might not execute -- consumption inside is not definite.
        self.ctx.func.current_consumed_own_params = consumed_before
        # loop_scope() restored the scope bindings; namespace mutations made
        # inside the body persist, so they are rolled back here.
        self._restore_ns_var_types(ns_types_before)
        self._sync_promoted_var_types()
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
        # The loop statement's own summary: its head (the target a `for`
        # binds, a walrus in a `while` head), body and `else`.
        cycle = self.ctx.fact_kills([stmt])
        if edges.breaks:
            # A `break` contributes no facts, only kills: the loop may have
            # left from any point of its cycle, where only the loop-entry
            # facts hold -- not what the head proves on its FALSE path nor
            # what the `else` clause establishes.
            self.init.join_entry_from_any_point(pre_loop, cycle)
            on_every_break = frozenset.intersection(*edges.breaks)
            f = self.ctx.func
            if f.init_terminated:
                # The `else` clause never falls out of the loop: the breaks
                # are its only way out, and they leave it live.
                f.definitely_assigned = set(on_every_break)
                f.init_terminated = False
            else:
                f.definitely_assigned &= on_every_break
            f.loop_bound_assigned &= on_every_break
        # An edge out of the body may leave through a `finally` that deletes
        # a name the edge saw bound: what the cycle deletes is unbound after.
        self.ctx.func.definitely_assigned -= cycle.deletes
        self.ctx.func.loop_bound_assigned -= cycle.deletes

    def _resolve_obj_storage(self, obj: TpyExpr) -> StorageKey | None:
        """This analyzer's spelling of `BorrowTracker.resolve_obj_storage`."""
        return self.ctx.func.borrow_tracker.resolve_obj_storage(obj)

    def _mark_field_write_views(self, target: TpyFieldAccess,
                                receiver_storage: StorageKey | None,
                                field_storage: StorageKey | None) -> None:
        """Demote the str/bytes views a ``X.f = v`` write clobbers.

        The write replaces exactly ``X.f`` (and whatever it owns), so the key
        is the FIELD path: keying on the receiver would demote a view of an
        untouched SIBLING field. The receiver is the key only when no field
        path can be spelled (a hop the tracker cannot name, e.g. a property
        getter in the chain), where the coarse mark is the only sound one.
        """
        key = field_storage or path_key(target) or receiver_storage
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
        root = holder_root(init.obj, MayLend(self.ctx), proven=True)
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
                self.ctx.registry)
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
        method, or the module's `__tpy_init` (a name first bound in one of
        its blocks is no module slot). A generator or async body keeps
        every local in its frame for the frame's whole life already."""
        fn = self.ctx.func.current_function
        return (isinstance(fn, TpyFunction)
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
        self.compat.advise_unneeded_copy(value)

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
                                 whole_slot: bool = True) -> bool:
        """Check that an lvalue returned as Own[T] has explicit copy() or is
        auto-moved; a borrowed one copies and warns, naming the element of a
        returned tuple. True when the slot copies."""
        return self.compat.check_own_lvalue_into_own(own_type, expr, context,
                                                     action="return",
                                                     whole_slot=whole_slot)

    def _warn_live_name_return(self, value: TpyExpr, expected: TpyType) -> None:
        """The `Own[T]` slot's copy rule at the other owning return slots --
        an `Optional` or a tuple with an `Own` part: an owned NAME a closure
        captures (it may read it after the return) is not handed over, and
        the slot says so in the same words."""
        value = self.compat.returned_captured_name(value)
        if value is None or not _slot_owns(expected):
            return
        t = self.ctx.get_expr_type(value)
        if t is None or not _holds_reference(t):
            return
        if self.ctx.is_type_non_copyable(t):
            reason = (self.ctx.nocopy_reason(t) if self.ctx.is_type_nocopy(t)
                      else f"non-copyable type '{unwrap_readonly(t)}'")
            self.ctx.copy_error(
                f"{reason} is used after this point and "
                f"cannot be moved into return type {expected}. Remove later "
                f"uses or restructure the code.", value, value)
            return
        shown = self.compat.diag_type(t)
        # An open payload's copy is the instantiation's answer: hedged, as
        # at every other owning sink.
        if self.ctx.defer_own_copy_verdict(shown, t, "owned storage", value,
                                           source=value):
            return
        remedy = self.compat.copy_remedy(value, note="")
        self.ctx.copy_warning(
            lambda n: f"copies {shown} into owned storage; {remedy}{n}",
            value, value, note=self.compat.binding_note(value))

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
            with self.pend.sink(stmt.value if is_numeric_slot(expected)
                                else None):
                ret_type = self.expr.analyze_at_slot(
                    stmt.value, expected, CoercionContext.RETURN)
            stmt.value_type = ret_type
            if value_leaves(ret_type):
                self.pend.defer(stmt, (ret_type,),
                                lambda ts: setattr(stmt, "value_type", ts[0]))
            written = stmt.value
            stmt.value = self.compat.coerce_expr(stmt.value, ret_type, expected, "return value",
                                                  coercion_ctx=CoercionContext.RETURN, is_return=True)
            self._mark_finally_deferred_return(stmt, written, ret_type,
                                               expected)
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
            ret_call = peel_coerce(stmt.value)
            for src in frame_operand_keys(ret_call):
                sited = self.ctx.sited_here(src)
                self.ctx.mark_param_returned(sited)
                self.ctx.mark_param_returned(sited, storage=True)
            # Check for a borrowed source returned as Own[T] without an
            # explicit copy(). `readonly` peels first: `readonly[Own[T]]`
            # is the same owning slot with a const view on top, so the
            # spelling must not be a way past the check.
            own_expected = unwrap_readonly(expected)
            # `-> Own[T] | None` takes a non-None value exactly as `-> Own[T]`
            # does (a tuple payload is the tuple checks' below).
            if (isinstance(own_expected, OptionalType)
                    and isinstance(unwrap_readonly(own_expected.inner), OwnType)
                    and own_tuple_target(own_expected.inner) is None):
                own_expected = unwrap_readonly(own_expected.inner)
            returned_name = peel_coerce(stmt.value)
            # A closure's return of a tuple it captured is decided per leaf
            # (`check_name_borrow_into_own`), never as a whole. Only a return
            # would move a whole name out; an `Own` argument copies a
            # captured name as any name it outlives, and a nested def
            # cannot yield.
            captured_tuple = (
                isinstance(returned_name, TpyName)
                and own_tuple_target(own_expected) is not None
                and self.compat.reads_capture(returned_name.name))
            if not captured_tuple:
                # A result owning a leaf is an owning sink, `Own` spelled
                # or not (an Optional of an `Own`-element tuple).
                self.compat.register_owned_result(
                    erased_source(stmt.value), own_expected, TupleSink.RETURN)
            if isinstance(own_expected, OwnType) and not captured_tuple:
                own_source = erased_source(stmt.value)
                if self.compat.is_copy_call(own_source):
                    self._warn_unnecessary_return_copy(own_source)
                else:
                    self._check_own_lvalue_return(
                        own_expected, own_source, "return type")
            # `-> X | None` returns its non-None value exactly as `-> X` does.
            tuple_slot = (expected.inner if isinstance(expected, OptionalType)
                          else expected)
            returned = peel_coerce(stmt.value)
            self.compat.check_tuple_result(stmt.value, expected,
                                           TupleSink.RETURN)
            tuple_target = own_tuple_target(tuple_slot)
            if (isinstance(returned, TpyTupleLiteral)
                    and tuple_target is not None):
                # The per-element capture mode (ref/value/const_ref) of the
                # literal a return hands out in borrow form.
                self._annotate_tuple_elem_capture(
                    returned, tuple_target, sink=TupleSink.RETURN,
                    sink_slot=tuple_slot,
                    return_slot=(expected if tuple_slot is not expected
                                 else None),
                    members_checked=True)
            # A tuple's per-leaf check leaves a name a closure pins to this
            # whole-value copy, at an `Own[tuple[...]]` slot too.
            if (not isinstance(own_expected, OwnType)
                    or own_tuple_target(own_expected) is not None):
                self._warn_live_name_return(stmt.value, own_expected)
            # Returning an ephemeral generator/iterator borrow lets it escape
            # its iteration step -- reject with the copy-out fix (before the
            # generic dangling check so the specific message wins).
            self._reject_ephemeral_escape(stmt.value, "return")
            # Check for dangling reference (returning local/temporary as reference)
            self.compat.check_dangling_reference(stmt.value, expected, stmt.loc, ret_type)
            # Returning a non-value type by reference takes the source's address.
            # Mark both loop vars and params so they keep T& (not const T&/const T*).
            if isinstance(stmt.value, TpyName):
                mark_roots(self.ctx, [StorageKey.of_name(stmt.value.name)],
                           MarkRequest(params=False, loop_vars=True))
                # Escape tracking: returning a nested def marks it as escaping
                if stmt.value.name in self.ctx.func.nested_def_names:
                    self.ctx.reject_resumable_nested_def_escape(
                        stmt.value.name, stmt)
                    self.ctx.mark_nested_def_escape(stmt.value.name)
            # A returned borrow -- a reference, a borrowing view, a borrow
            # tuple's element pointers -- records its provenance and, unless
            # the slot is readonly or a view, keeps its roots writable.
            if expected is not None:
                self.ctx.escape_marks(stmt.value, expected,
                                      position=EscapePosition.RETURN)

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
            # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
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

    def _mark_finally_deferred_return(self, stmt: TpyReturn, value: TpyExpr,
                                      ret_type: TpyType,
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
        for _, leaf in tuple_literal_leaves(value):
            if isinstance(leaf, TpyName):
                self.ctx.mark_own_param_consumed(leaf.name)
                # The last-use mark does not see a finally's reads through an
                # alias or a closure, so a move there could leave the finally
                # reading a moved-from object: always refuse.
                if leaf in self.ctx.finally_rebound_returns:
                    self._check_eager_return_copyable(leaf, leaf)
        if isinstance(value, TpyTupleLiteral):
            self._mark_finally_deferred_tuple_return(stmt, value, expected)
            return
        # The capture moves the local itself out after the chain, so a name
        # the return converts (other than an identity coercion) stays eager.
        if not isinstance(value, TpyName) or _converts(stmt.value, value):
            return
        if self._mark_finally_deferred_tuple_name(stmt, value, expected):
            return
        if not (value in self.ctx.finally_return_candidates
                and self._defers_to_settlement(value.name)):
            return
        exp = unwrap_ref_type(expected)
        val = unwrap_readonly(unwrap_own(unwrap_ref_type(ret_type)))
        declared = self._declared_binding(value.name)
        eligible = False
        if (result_owns_storage(exp)
                and not unwrap_own(exp).copy_is_cheap()
                and not isinstance(exp, OptionalType)):
            eligible = self._own_slot_deferrable(declared, val)
        elif isinstance(exp, OptionalType) and not exp.uses_pointer_repr():
            # Shape B: pointer-repr Optional local returned as the storage
            # Optional (Own[T] | None) -- the ptr_to_optional_move arm. The
            # analyzed type may be narrowed to T, so key on the declared
            # binding.
            eligible = pointer_repr_optional(declared) is not None
            # An owned nullable tuple local holds the slot's own by-value
            # optional, so it defers like an Own[T] local (shape A): the
            # finally's write through it must reach the returned tuple.
            if (not eligible and isinstance(declared, OptionalType)
                    and not declared.uses_pointer_repr()):
                inner = unwrap_readonly(declared.inner)
                eligible = (isinstance(inner, TupleType)
                            and any(isinstance(unwrap_readonly(et), OwnType)
                                    and not unwrap_readonly(
                                        unwrap_own(et)).copy_is_cheap()
                                    for et in inner.element_types))
        if eligible:
            restored = value not in self.ctx.all_last_uses
            self.ctx.all_last_uses.add(value)
            stmt.finally_deferred_capture = True
            stmt.finally_deferred_name = value
            self.compat.record_owning_read(FinallyCapture(
                value.name, value, stmt, None,
                restored_last_use=restored))

    def _defers_to_settlement(self, name: str) -> bool:
        """A returned name whose finally-deferred capture is marked now: a
        body local's, which settlement confirms from what its binding owns
        (`_settle_finally_capture`), or an owned parameter's."""
        return (self.compat.settles_body_local(name)
                or self.compat._is_owned_var(name))

    def _denotes_owned_binding(self, expr: TpyExpr) -> bool:
        """Whether `expr` is a whole binding that owns its value -- a name,
        or a walrus, which denotes its target (`is_rvalue_source`) -- on
        the ownership fact as far as the body has been read
        (`_is_owned_var`): the body's settled fact comes only after its
        last statement, past the point a declaration's type is formed."""
        e = peel_coerce(expr)
        name = (e.name if isinstance(e, TpyName)
                else e.target if isinstance(e, TpyNamedExpr) else None)
        return name is not None and self.compat._is_owned_var(name)

    def _names_unowned_storage(self, expr: TpyExpr) -> bool:
        """Whether `expr` names existing storage it does not own: a place
        (`is_rvalue_source` false) that is no whole owning binding, or a
        select each of whose arms is one or `None` (`t[0] if c else None`),
        which the value category reads as fresh because its type carries
        the `Own` marker. A select nested in an arm is not walked: the
        lowering binds none (`expr.ifexpr`), so it keeps the marker."""
        def unowned_place(a: TpyExpr) -> bool:
            return (not isinstance(a, TpyIfExpr)
                    and not is_rvalue_source(self.ctx, a)
                    and not self._denotes_owned_binding(a))
        e = peel_coerce(expr)
        if isinstance(e, TpyIfExpr):
            arms = [peel_coerce(a) for a in (e.then_expr, e.else_expr)]
            places = [a for a in arms if not isinstance(a, TpyNoneLiteral)]
            return bool(places) and all(unowned_place(a) for a in places)
        return unowned_place(e)

    def _owned_element_is_pointer(self, name: str, index: int) -> bool:
        """Whether the tuple `name` this body OWNS holds element `index` as a
        pointer to an object outside it (`std::tuple<A, B*>` -- a mixed
        parameter or a mixed call result, the only owned tuples with a
        borrowed element): what that element names outlives a move of
        `name`. A module global holds its elements by value, and a name this
        body does not own is never moved here, so the answer does not matter
        for it."""
        if self.ctx.is_top_level or not self.compat._is_owned_var(name):
            return False
        bare = self._declared_binding(name)
        return (isinstance(bare, TupleType)
                and index < len(bare.element_types)
                and TupleType._element_is_pointer_repr(bare.element_types[index]))

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
                and not val.copy_is_cheap()
                and not isinstance(val, (OptionalType, UnionType, TupleType))
                and not is_protocol_type(val))

    def _mark_finally_deferred_tuple_return(self, stmt: TpyReturn,
                                            value: TpyTupleLiteral,
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
        leaves_all = tuple_literal_leaves(value)
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
                    and self._defers_to_settlement(leaf.name)):
                continue
            slot = self._tuple_slot_at(exp, path)
            if slot is None:
                continue
            payload = owned_payload(slot)
            owned = (whole_owned or payload is not None
                     or result_owns_storage(slot))
            slot_t = payload if payload is not None else unwrap_readonly(slot)
            if not owned or slot_t.copy_is_cheap():
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
        leaves = dict(tuple_literal_leaves(value))
        for path in paths:
            restored = leaves[path] not in self.ctx.all_last_uses
            self.ctx.all_last_uses.add(leaves[path])
            # The deferred member is moved out after the chain: a consume.
            self.ctx.mark_own_param_consumed(leaves[path].name)
            self.compat.record_owning_read(FinallyCapture(
                leaves[path].name, leaves[path], stmt, path,
                restored_last_use=restored))
        stmt.finally_deferred_leaves = tuple(paths)
        stmt.finally_deferred_capture = True

    def _mark_finally_deferred_tuple_name(self, stmt: TpyReturn,
                                          value: TpyName,
                                          expected: TpyType) -> bool:
        """`return t` of an owned tuple local into an owning tuple slot: the
        whole tuple is the deferred member (path `()`), captured like the
        scalar `return b` when the lowering finds the local's storage is the
        return's own C++ type, else kept eager."""
        exp = unwrap_readonly(unwrap_ref_type(expected))
        whole_owned = isinstance(exp, OwnType)
        if whole_owned:
            exp = unwrap_readonly(exp.wrapped)
        if not isinstance(exp, TupleType):
            return False
        if not (whole_owned or any(
                (payload := owned_payload(et)) is not None
                # Not `copy_is_cheap`: whether a value-only local's C++ is
                # the slot's own (no per-element conversion) is decided
                # only in the lowering, so such a tuple stays eager.
                and not payload.is_value_type()
                for et in exp.element_types)):
            return False
        if not (value in self.ctx.finally_return_candidates
                and self._defers_to_settlement(value.name)
                and isinstance(self._declared_binding(value.name),
                               TupleType)):
            return False
        restored = value not in self.ctx.all_last_uses
        self.ctx.all_last_uses.add(value)
        self.ctx.mark_own_param_consumed(value.name)
        stmt.finally_deferred_leaves = ((),)
        stmt.finally_deferred_capture = True
        owned = (None if whole_owned else frozenset(
            i for i, et in enumerate(exp.element_types)
            if unwrap_optional_own(et) is not None))
        self.compat.record_owning_read(FinallyCapture(
            value.name, value, stmt, (), restored_last_use=restored,
            owned_elements=owned))
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

    def _mark_bound_loop_var_sources(self, name: str, var_type: TpyType,
                                     value: TpyExpr) -> None:
        """A REASSIGNED local bound off a loop var points into it with one
        pointer type for every referent (`T* x = &(var)`), and a reassigned
        borrow tuple takes its element pointers out of it the same way, so
        the loop var cannot bind const. A local bound once is a read-only
        bind: it takes the loop var's constness, and a write through it
        climbs to the loop var (`mark_binding_written`) -- unless it becomes
        a pointer slot (`ScopeTracker._hoist`; a resumable body's frame
        local). The source may be the loop var itself, any field / element
        chain rooted at it (`x = h.b`), or each operand a select may pick.
        One rule for a declaration and a plain assignment."""
        cur = self.ctx.func.current_function
        frame_slot = isinstance(cur, TpyFunction) and (cur.is_generator
                                                       or cur.is_async)
        if (name not in self.ctx.func.current_reassigned_vars
                and not frame_slot):
            return
        if not (not var_type.is_value_type()
                or _local_points_into_source(var_type)):
            return
        mark_roots(self.ctx, storage_roots(value, Use.HOLDER, None,
                                           MayLend(self.ctx)),
                   MarkRequest(params=False, loop_vars=True))

    def _mark_rebound_roots(self, var_type: TpyType, value: TpyExpr, *,
                            rebind: bool) -> None:
        """A reassigned local that points into its source -- a reference
        local (`T* p = &(src)`), a borrow or pointer tuple's element
        pointers -- marks the roots its value points into written, so they
        stay `T&`. Asked of the type alone, so the same whatever order the
        name's bindings come in; asked here, at the binding, because a
        local or loop-variable root climbs through borrows as they stand
        now (`mark_param_mutated`). A tuple's roots are the root rule's
        (`binding_roots`: each ternary arm, each borrowed literal element,
        what a call may lend); the scalar keeps the place walk, which
        reads a handle's referent and a call's receiver lend only."""
        if not _local_points_into_source(var_type):
            return
        bare = unwrap_readonly(unwrap_ref_type(var_type))
        if (isinstance(bare, TupleType)
                or (isinstance(bare, OptionalType)
                    and bare.wraps_pointer_repr_tuple())):
            mark_roots(self.ctx, [StorageKey.of_name(n) for n in
                                  binding_roots(self.ctx, value) or ()],
                       MarkRequest())
        elif not (rebind and not self.compat.is_lvalue(value)):
            mark_roots(self.ctx, storage_roots(value, Use.PLACE,
                                               self.ctx.get_expr_type,
                                               MayLend(self.ctx)),
                       MarkRequest())

    def _annotate_tuple_elem_capture(
        self, literal: TpyTupleLiteral, tuple_type: TupleType,
        *, sink: TupleSink = TupleSink.LOCAL,
        sink_dest: str = "owned storage",
        sink_slot: TpyType | None = None,
        return_slot: OptionalType | None = None,
        members_checked: bool = False
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
            sink_slot: The slot the sink fills, as declared (an
                `Own[tuple[...]]` return owns every nested leaf too);
                `tuple_type` when not given.
            return_slot: The declared nullable return `tuple_type` is the
                non-None value of, named as such in a remedy.
            members_checked: The members took their copy rule already: a
                walrus bound the literal (the local's rule, the sink copying
                the name's read), or the result check ran it
                (`check_tuple_result`).
        """
        if sink is TupleSink.LOCAL and not members_checked:
            self.compat.check_tuple_literal_members(
                literal, tuple_type, sink, sink_dest)
        elif not members_checked:
            self.compat.check_owned_tuple_sink(
                literal, sink_slot if sink_slot is not None else tuple_type,
                sink_dest, literal, sink=sink)
        is_return = sink is TupleSink.RETURN
        # A field and a container element both OWN what they store.
        is_field = sink in (TupleSink.FIELD, TupleSink.CONTAINER)
        V = TupleElemCapture.VALUE
        R = TupleElemCapture.REF
        CR = TupleElemCapture.CONST_REF

        literal.elem_capture = []
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(literal.elements):
                literal.elem_capture.append(V)
                continue
            # The declared element type is the contract of a returned element:
            # only a `readonly[...]` element hands out a const borrow.
            elem_declared_readonly = isinstance(unwrap_ref_type(et), ReadonlyType)
            elem = literal.elements[i]

            # Value types, Own[T], and TypeParamRef are always VALUE.
            if et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)):
                literal.elem_capture.append(V)
                continue

            # Owned storage (a field, a container element): every
            # reference-type element is held by value -- an owned local or
            # param at its last use MOVES in (and is consumed, as the scalar
            # `self.f = q` consumes it), a borrowed one is copied.
            if is_field:
                # The member check above decided (and recorded) the move.
                if isinstance(elem, TpyName):
                    self.compat.check_own_consumption(elem)
                literal.elem_capture.append(V)
                continue

            # Return context
            if is_return:
                if self.compat.is_dangling_return(elem):
                    # Will error separately in check_dangling_reference
                    literal.elem_capture.append(V)
                elif self.compat.is_const_ref_source(elem):
                    if elem_declared_readonly:
                        literal.elem_capture.append(CR)
                    else:
                        fix = TupleType(tuple(
                            make_readonly(t) if j == i else t
                            for j, t in enumerate(tuple_type.element_types)))
                        slot: TpyType = tuple_type
                        if return_slot is not None:
                            slot, fix = return_slot, return_slot.with_inner(fix)
                        raise self.ctx.error(
                            f"Cannot return readonly[{et}] as tuple element {i} "
                            f"{self.compat.readonly_return_hint(slot, et, fix)}",
                            elem
                        )
                elif elem_declared_readonly:
                    literal.elem_capture.append(CR)
                else:
                    literal.elem_capture.append(R)
                    # Returning a non-value element by reference takes its
                    # address, so the sources keep T& (not const T&); the
                    # climb reaches a field-path borrow root as the scalar
                    # return's does.
                    mark_roots(self.ctx, storage_roots(
                        elem, Use.PLACE, self.ctx.get_expr_type,
                        MayLend(self.ctx)),
                        MarkRequest(through_field=True))
                continue

            # Local context: is_const_ref_source handles ReadonlyType
            # (including constructor params which are typed as ReadonlyType)
            # Pointer-form Optional: force REF for all sources (including
            # non-lvalue None) so the tuple has a uniform slot shape. A
            # mixed `(t, None)` would otherwise yield std::tuple<T*,
            # std::optional<T>> and not match a uniformly pointer-form
            # downstream type.
            et_opt = pointer_repr_optional(et)
            if et_opt is not None:
                if (not isinstance(elem, TpyNoneLiteral)
                        and not self.compat.is_lvalue(elem)
                        and self.ctx.is_type_non_copyable(et_opt.inner)):
                    raise self.ctx.error(
                        f"cannot bind tuple element {i} of non-copyable type "
                        f"'{et_opt.inner}' from rvalue. Bind to a local first, or "
                        f"place the literal directly at its consumer"
                        f"{NOCOPY_REMEDIATION_HINT}",
                        elem,
                    )
                if self.compat.is_const_ref_source(elem):
                    literal.elem_capture.append(CR)
                else:
                    literal.elem_capture.append(R)
                continue
            if not self.compat.is_lvalue(elem):
                literal.elem_capture.append(V)
            elif self.compat.claim_name_move(elem, OwnType(et),
                                             f"tuple element {i}",
                                             whole_slot=False):
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
                    # can't stay the default `const T&` borrow (the
                    # return-context marking above).
                    mark_roots(self.ctx, storage_roots(
                        elem, Use.PLACE, self.ctx.get_expr_type,
                        MayLend(self.ctx)),
                        MarkRequest(through_field=True))

    def _unfold_loop_killed_isinstance(
            self, condition: TpyExpr, killed: set[str]) -> None:
        """Drop the static-true/false `isinstance` fold on a loop condition
        whose subject the loop body reassigns.

        The fold is computed from the subject's loop-entry narrowing; a rebind
        in the body makes it non-loop-invariant, so the condition must be
        re-checked each iteration (clearing `macro_expansion` routes codegen to
        the runtime `holds_alternative`/cast). Walks the boolean structure so
        `isinstance(t, T) and ...` is covered too.
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
                merged = make_readonly(current)
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
                target = make_readonly(canonical)
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

    def _check_constant_write(self, target: TpyExpr, stmt: TpyStmt) -> None:
        """Reject `=` / `+=` on Final class constants and on another module's
        Final variables (`sys.platform = ...`); warn on instance-side
        writes to mutable ClassVar (CPython would create an instance attribute
        instead of writing through to class storage -- mypy/pyright already
        flag this; matching them keeps the tooling story consistent).
        """
        if not isinstance(target, TpyFieldAccess):
            return
        module_name = self.ctx.module_named_by(target.obj)
        if module_name is not None:
            module_info = self.ctx.registry.get_module(module_name)
            var = module_info.variables.get(target.field) if module_info is not None else None
            if var is not None and var.is_final:
                raise self.ctx.error(
                    f"Cannot reassign Final variable '{module_name}.{target.field}'",
                    stmt,
                )
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
            lost = copy_result_under_write(target.obj)
            if lost is not None:
                raise self.ctx.error(lost_write_message(lost), target)
            obj_type = self.ctx.get_expr_type(target.obj)
            if obj_type is not None:
                check_type = obj_type
                if isinstance(check_type, OptionalType):
                    check_type = check_type.inner
                if (isinstance(check_type, ReadonlyType)
                        # isinstance narrowing strips ReadonlyType from the
                        # expr type; the scope binding preserves it.
                        or isinstance(target.obj, TpyName)
                        and self.ctx.is_readonly_name(target.obj.name)):
                    raise self.ctx.error(
                        "Cannot mutate readonly reference"
                        + implicit_readonly_result_hint(self.ctx, target.obj), target)
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
                        info = self.ctx.registry.get_record_for_type(actual)
                        if info is not None:
                            in_own_init = (
                                in_any_init
                                and info is self.ctx.registry.get_record(rec.name)
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
                                            # A readonly handle field cannot be
                                            # re-pointed; what it points at
                                            # stays writable either way.
                                            repoint = (
                                                f"; declare the field '{fld.type.wrapped}' to re-point it"
                                                if is_indirection_type(fld.type.wrapped) else "")
                                            raise self.ctx.error(
                                                f"Cannot assign to readonly field '{target.field}'{repoint}",
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
        fi = consuming_iter_of(iterable_type, self.ctx.registry)
        if fi is None:
            return None
        # Build type substitution for generic types
        type_subst = self.type_ops.build_type_substitution(iterable_type)
        if type_subst:
            fi = self.type_ops.substitute_method_type_params(fi, type_subst)
        return fi

    def _resolve_enum_iterable(self, stmt: TpyForEach) -> NominalType | None:
        """Check if for-each iterates over an enum type (e.g. `for c in Color`).

        Returns the enum NominalType if so, None otherwise.
        """
        ref = self.ctx.type_ref(stmt.iterable)
        if ref is None or ref.kind != SymbolKind.ENUM:
            return None
        return ref.info

    def _decide_awaiting_lists(self) -> None:
        """A list literal handed out undecided for a typed container slot
        is decided by the coercion to that slot. One no coercion reached by
        the end of its statement is decided from what it holds, like any
        use that needs the element type."""
        waiting = self.ctx.func.awaiting_container
        if not waiting or self.ctx.trial_depth:
            return
        self.ctx.func.awaiting_container = []
        for cid, expr in waiting:
            if self.ctx.pending_num_cells[cid].settled is None:
                self.pend.settle({cid}, use=expr,
                                 what=self.expr.describe_pending_use(expr))
        self.pend.resolve_ready()

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
            # Stopgap until MIR models the step loan
            # (BUGS.md#next-step-reference-outlived-by-advance).
            twice = iterator_advanced_twice(self.ctx, stmt.exprs())
            if twice is not None:
                raise self.ctx.error(
                    f"{twice[0].call_display} advances '{twice[1]}' twice "
                    f"in one expression; bind the first step to a name",
                    twice[0])
            self._check_retained_genexprs(stmt)
            self._decide_awaiting_lists()
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
            self.init.flush_pending_ptr_facts()
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
                # Each pull of the kept frame stores into the enclosing
                # variables its walruses bind, and any later call may pull it.
                self.ctx.func.closure_written_names |= (
                    set(func.genexpr_nonlocals) | set(func.genexpr_globals))
                reject_rebound_iterated_capture(
                    self.ctx, gx, func,
                    [n for n in func.capture_params if later is None or n in later],
                    "it is kept past the statement and can be rebound between pulls")
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
            # A method's result the statement drops needs no type: what it
            # hands out of a container whose leaves are open stays open.
            call = stmt.expr if isinstance(stmt.expr, TpyMethodCall) else None
            with self.pend.sink(call), self.pend.list_sink(call):
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
            head = self.expr.analyze_branch(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(
                head.proven_types(True))
            stmt.else_type_facts = self._filter_union_codegen_facts(
                head.proven_types(False))
            scope_before = set(self.ctx.func.current_scope.bindings.keys())
            pending_before = set(self.ctx.func.pending_loop_vars)
            assigned_before = frozenset(self.ctx.func.definitely_assigned)
            before = self.init.save()
            consumed_before = self.ctx.func.current_consumed_own_params.copy()
            # Save binding types for ReadonlyType merge after branches
            bindings_before = dict(self.ctx.func.current_scope.bindings)
            ns_types_before = self._save_ns_var_types()
            self.init.restore(head.on_true)
            for s in stmt.then_body:
                self.analyze_stmt(s)
            then_state = self.init.save()
            consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
            then_terminated = self.ctx.func.init_terminated
            bindings_after_then = dict(self.ctx.func.current_scope.bindings)
            pending_after_then = set(self.ctx.func.pending_loop_vars)
            carried_else = self._enter_sibling_arm(
                bindings_before, ns_types_before, head.on_false)
            self.ctx.func.current_consumed_own_params = consumed_before.copy()
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
            declared_before = set(self.ctx.func.var_scope_depth)
            consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
            # Save namespace types -- loop_scope() restores scope bindings
            # automatically, but namespace mutations inside the loop persist.
            ns_types_before_while = self._save_ns_var_types()
            first_pass = self.init.save()
            body_kills = self.ctx.fact_kills(stmt.body,
                                            extra_exprs=[stmt.condition])
            # The head runs again before every pass: it is analyzed in the
            # loop-entry state, with what the back edge kills dropped, and
            # the body is entered from its TRUE path.
            self.init.apply_fact_kills(body_kills, keep_steps=True)
            head = self.expr.analyze_branch(stmt.condition)
            # Whether the first pass runs: the pre-loop state, less what the
            # head's own evaluation may write before its test is read.
            entry = self.init.save()
            self.init.restore(first_pass)
            runs_once = self.narrowing.condition_provably_true(
                stmt.condition,
                lambda operand: self.init.apply_fact_kills(self.ctx.fact_kills(
                    [], extra_exprs=[operand])))
            self.init.restore(entry)
            # A walrus the head declares binds an object of its own again
            # on every pass, so for the body it is a loop-body binding: an
            # alias kept from an earlier pass is the declaration twin's
            # shared-slot case. A binder that borrows shares no slot.
            head_binders = sorted(
                n for n in condition_walrus_targets(stmt.condition)
                - declared_before if n in self.ctx.func.owned_locals)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(
                head.proven_types(True))
            # An `isinstance` fold rests on the loop-entry narrowing of its
            # subject; if the body rebinds that subject the condition is not
            # loop-invariant, so drop the static fold and re-check each
            # iteration (else codegen emits an exit-less `while (true)`).
            self._unfold_loop_killed_isinstance(stmt.condition, body_kills.names)
            with self._loop_body_scope(stmt) as (inner_scope, edges):
                self.init.restore(head.on_true)
                saved_binders = {n: self.ctx.local_decl_of(n)
                                 for n in head_binders}
                for n, (_depth, block) in saved_binders.items():
                    self.ctx.declare_local(n, inner_scope.depth,
                                           block_depth=block)
                try:
                    for s in stmt.body:
                        self.analyze_stmt(s)
                finally:
                    for n, saved in saved_binders.items():
                        self.ctx.restore_local_decl(n, saved)
            self._finish_loop_clauses(
                stmt, inner_scope, edges, before=head.on_false,
                consumed_before=consumed_before_loop,
                ns_types_before=ns_types_before_while, runs_once=runs_once,
                exit_from_head=True, pre_loop=first_pass)
        elif isinstance(stmt, TpyForEach):
            # The head unpack is over the loop's per-iteration holder whoever
            # built the node (parser, genexpr, a macro), so the ForEach's own
            # fact is what marks it.
            head = stmt.head_unpack()
            # The rebinds a binding of the loop sees: each pass binds the
            # variable (or the head's targets) afresh, so only its own body's
            # count. Decided once here, for every binding the loop makes.
            stmt.loop_body_rebinds = frozenset(scan_reassigned_vars(
                stmt.body[1:] if head is not None else stmt.body,
                pre_declared=set(stmt.head_targets()))
                .reassigned)
            if head is not None:
                head.is_loop_head = True
                head.loop_body_rebinds = stmt.loop_body_rebinds
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
                        before, kills=self.ctx.fact_kills(
                            stmt.body))
                    with self.scopes.loop_var(inner_scope, stmt.var, elem_type, inner_scope.depth, is_foreach=True,
                                              binder=stmt):
                        for s in stmt.body:
                            self.analyze_stmt(s)
                self._finish_loop_clauses(
                    stmt, inner_scope, edges, before=before, pre_loop=before,
                    consumed_before=consumed_before_loop,
                    ns_types_before=ns_types_before_foreach,
                    runs_once=runs_once, elem_type=elem_type)
            else:
                self.ctx.for_head_bodies.append(stmt.body)
                try:
                    # Iterating hands out elements, each a pending read that
                    # follows the cell like `xs[i]` does: the statement itself
                    # decides no width -- unless the loop variable is read
                    # after the loop, which still decides it (TODO.md slice
                    # (7d): a binding deciding its declaration once).
                    decides = stmt.var_live_after is not False
                    with self.pend.list_sink(None if decides
                                             else stmt.iterable):
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
                note_pending_elem_read(self.ctx, inner_iterable_type,
                                       stmt.iterable)
                # Elements from a readonly iterable inherit readonly status
                if is_readonly_iterable:
                    elem_type = make_readonly(elem_type)
                elem_type = self._infer_new_local_type(
                    stmt.var, elem_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                stmt.elem_type = make_ref(elem_type)

                # Auto-consuming decision is deferred until after body analysis
                # (see below) so we know whether the loop var is mutated.

                elem_source = iter_element_source(
                    resolved_for_iter
                    if is_protocol_type(resolved_for_iter)
                    else inner_iterable_type, self.ctx.registry)
                self._stamp_step_handover(stmt,
                                          unwrap_ref_type(stmt.elem_type))
                # Each step, the first one before the body included, is a
                # call when it runs TPy code.
                pulls_run_code = record_iteration_pull(
                    self.ctx, stmt.iterable, iterable_type)
                if pulls_run_code:
                    self.narrowing.invalidate_for_code_run()
                # Decided before the body: the ranges the head is read
                # against are the pre-loop ones, as on the `while` side.
                runs_once = self.narrowing.for_head_provably_runs(stmt)
                before = self.init.save()
                consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
                ns_types_before_foreach = self._save_ns_var_types()
                with self._loop_body_scope(stmt) as (inner_scope, edges):
                    entry_kills = self.ctx.fact_kills(
                        stmt.body)
                    entry_kills.calls |= pulls_run_code
                    self.init.apply_loop_entry_facts(before, kills=entry_kills)
                    # Track range facts for loop variable from range() calls
                    self._track_for_range_facts(stmt)
                    check_iter_receiver_loans(
                        self.ctx, stmt.iterable, inner_iterable_type,
                        loop_var=True)
                    iterates = self._register_foreach_iter_loans(
                        stmt, iterable_type)
                    # A user `__iter__` that mutates its receiver needs a
                    # non-const receiver; record that so an enclosing read-only
                    # method isn't wrongly inferred const (the loop_var_iterable
                    # self-tracing set above must already be populated).
                    _record_iter_receiver_mutation(
                        self.ctx, stmt.iterable, inner_iterable_type,
                        check_loans=False, loop_var=True)
                    if elem_source in (IterElementSource.HANDLE,
                                       IterElementSource.PROTOCOL):
                        iter_depth = inner_scope.depth
                    elif elem_source in (IterElementSource.CONTAINER,
                                         IterElementSource.FRESH_ITERATOR):
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
                        if elem_source is IterElementSource.CONTAINER:
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
                    self.ctx.func.consumed_loop_vars.discard(stmt.var)
                    self._check_loop_var_rebind(stmt, elem_type)
                    cid_floor = self.ctx.pending_num_counter
                    var_type, declared = self._loop_var_cell_type(
                        stmt, elem_type)
                    self._declare_loop_var(stmt, declared)
                    with self.scopes.loop_var(inner_scope, stmt.var, var_type, iter_depth, is_foreach=True,
                                              binder=stmt):
                        # Inside the declaration: the block rule reads the
                        # loop variable's own depth when it notes the binding.
                        self._note_loop_var_iteration(
                            stmt.var, elem_type, iterates,
                            placeable=not iteration_yields_copies(
                                self.ctx, iterable_type))
                        for s in stmt.body:
                            self.analyze_stmt(s)
                    # The head's variables leave scope with the loop: a later
                    # binding of the name is another variable.
                    self.pend.retire_loop_heads(cid_floor)
                    if track_loop_prov:
                        self.init.remove_loop_var_provenance(stmt.var)
                    for name in eph_added:
                        self.ctx.func.ephemeral_borrow_vars.pop(name, None)
                # Whether the loop var binds const-ref is decided once every
                # write through it is known, the callees' included
                # (`resolve_loop_bindings`); here only whether it may at all.
                # Only for non-value types or expensive-to-copy value types
                # (BigInt, String, tuples with expensive elements). Cheap
                # primitives (int32_t, bool, double, etc.) are better copied
                # into a register than referenced through a pointer.
                # For synthetic tuple-unpack loop vars, skip const binding when
                # the unpack has elements that need mutable references --
                # const tuple prevents T& bindings via std::get.
                unwrapped = unwrap_qualifiers(elem_type)
                needs_mut_unpack = False
                if stmt.is_tuple_unpack and stmt.body:
                    first = stmt.body[0]
                    if isinstance(first, TpyTupleUnpack) and any(first.is_ref):
                        needs_mut_unpack = True
                self.ctx.loop_binders.append((stmt, (
                    worth_const_ref(elem_type)
                    and stmt.var not in self.ctx.func.consumed_loop_vars
                    and not needs_mut_unpack)))
                # Auto-consuming iteration: use consuming __iter__ when the
                # container is at last use and the loop var is MOVED OUT
                # (`claim_loop_var_move`: an owning sink at its last use, no
                # hidden borrower). The container is dead after the loop, so
                # moving it into OwnIter is free (pointer swap), and the
                # moved elements are not copied. A loop var that is only
                # written avoids no copy by consuming: it borrows the
                # container, which outlives every borrow of an element.
                loop_var_needs_ownership = (
                    stmt.var in self.ctx.func.consumed_loop_vars)
                consuming_fi = None
                if (loop_var_needs_ownership
                        and not stmt.hoist_loop_var
                        and not unwrapped.is_value_type()
                        and isinstance(stmt.iterable, TpyName)):
                    consuming_fi = self._find_consuming_iter(inner_iterable_type)
                    loop_read = (ConsumingLoop(
                        stmt.iterable.name, stmt,
                        self._consuming_loop_revert(
                            stmt, iterable_type, elem_type))
                        if consuming_fi is not None else None)
                    if loop_read is None:
                        self.compat.retract_hidden_borrowed_last_use(
                            stmt.iterable)
                    elif not self.compat.claim_name_move(stmt.iterable,
                                                         read=loop_read):
                        consuming_fi = None
                if consuming_fi is not None:
                    stmt.consuming_iter_fi = consuming_fi
                self._finish_loop_clauses(
                    stmt, inner_scope, edges, before=before, pre_loop=before,
                    consumed_before=consumed_before_loop,
                    ns_types_before=ns_types_before_foreach,
                    runs_once=runs_once,
                    elem_type=unwrap_ref_type(stmt.elem_type))
                self._decide_loop_copies_var(stmt, iterable_type, elem_type)
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
            head = self.expr.analyze_branch(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(
                head.proven_types(True))
            if stmt.message is not None:
                # The message runs only on the raising path, inside the
                # statement's own failure branch, which never comes back.
                self.init.restore(head.on_false)
                msg_type = self.expr.analyze_expr(stmt.message)
                if not is_any_str_type(msg_type):
                    raise self.ctx.error(
                        "assert message must be a string", stmt)
            self.init.restore(head.on_true)
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
                or _polymorphic_subclass_fact(name, ty))
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

        if stmt.raise_expr is None:
            if not self.ctx.in_function_body():
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}' can only be used inside a function", stmt)
            record = self._raised_class(stmt.exception_type)
            if record is not None:
                self._raise_class(stmt, record, stmt.exception_type, None)
                return
            # Not a class: a variable of an exception type.
            stmt.raise_expr = TpyName(name=stmt.exception_type, loc=stmt.loc)
            stmt.exception_type = None
        self._analyze_raise_expr(stmt)

    def _raise_class(self, stmt: TpyRaise, record: 'RecordInfo', spelled: str,
                     operand: 'TpyExpr | None') -> None:
        """A raise of the class `record`, spelled `spelled`: the type form
        (`raise E`, `operand` the dotted path or None) or a construction
        (`operand` the call). The construction is analyzed as the
        expression it is; the statement adds only its own rules -- the
        tier, the `@error_return` match, the exception base, the
        @virtual_raise dispatch."""
        func = self.ctx.func.current_function
        qualified_exc = self._exception_qname(spelled, record)
        is_cf = record.is_return_exception or is_return_exception(qualified_exc)

        if is_cf:
            # Return-tier: must be inside @error_return(E) function with matching E
            if func.error_return is None:
                raise self.ctx.error(
                    f"'raise {spelled}' requires "
                    f"@error_return({spelled}) on the enclosing function",
                    stmt)
            if not error_return_matches(func.error_return, qualified_exc):
                raise self.ctx.error(
                    f"'raise {spelled}' does not match "
                    f"@error_return({spelled})", stmt)
        else:
            # Throw-tier: must inherit from Exception
            if not is_exception_record(record, self.ctx.registry):
                raise self.ctx.error(
                    f"'{spelled}' is not an exception type; "
                    f"it must inherit from Exception",
                    stmt)

        # CPython's `raise E` raises `E()`. A class without an `__init__`
        # raised with nothing written stays the type form, default-built.
        has_init = bool(self.ctx.registry.ctor_overloads(record))
        if isinstance(operand, (TpyCall, TpyMethodCall)):
            written = (source_view(operand).operands or operand.type_args
                       or operand.type_args_parse_error is not None)
            construction = operand if written or has_init else None
        elif has_init:
            construction = TpyCall(class_callee(record, stmt.loc), [],
                                   loc=stmt.loc)
        else:
            construction = None
        if construction is None:
            stmt.raise_expr = None
            stmt.exception_type = qualified_exc
            stmt.resolved_type = record_owner(record)
        else:
            stmt.raise_expr = construction
            stmt.exception_type = None
            stmt.resolved_type = self.expr.analyze_expr(construction)
        # @virtual_raise classes dispatch in their C++ __raise__ (e.g.
        # OSError's ctor-time errno -> subclass mapping); the fresh
        # construction must route through it, not the throw peephole.
        stmt.raise_via_virtual = record.virtual_raise and not is_cf
        self.init.mark_terminated()

    def _raised_class(self, name: str) -> 'RecordInfo | None':
        """The class a raise names by `name` (`raise E`), resolved like any
        type path; None when the name is not a class."""
        ref = self.ctx.type_ref_path([name])
        return ref.info if ref is not None and ref.kind == SymbolKind.RECORD else None

    def _raised_operand_class(self, raised: TpyExpr
                              ) -> 'tuple[RecordInfo, str] | None':
        """The class a raise operand constructs (`E(args)`, `Outer.Err(1)`,
        `m.EB[int32](x)`) or names (`m.Err`), resolved as the callee of
        that call is, with how it is spelled; None for a value operand."""
        if isinstance(raised, TpyCall):
            if not isinstance(raised.func, TpyName):
                return None
            ref = self.ctx.class_ref(raised.func)
            path = [raised.func.name]
        elif isinstance(raised, TpyMethodCall):
            if not isinstance(raised.obj, (TpyName, TpyFieldAccess)):
                return None
            ref = self.ctx.type_ref(raised.obj, raised.method)
            head = name_path(raised.obj)
            path = [*head, raised.method] if head is not None else []
        elif isinstance(raised, TpyFieldAccess):
            ref = self.ctx.type_ref(raised)
            path = name_path(raised) or []
        else:
            return None
        if ref is None or ref.kind != SymbolKind.RECORD:
            return None
        return ref.info, ".".join(path)

    def _exception_qname(self, spelled: str, record: 'RecordInfo') -> str:
        """The qualified name an exception class is matched by against
        `@error_return(...)`: a bare spelling qualifies as the decorator's
        does (`qualify_exception_name`), a dotted path by the class it
        resolved to."""
        if "." not in spelled:
            return qualify_exception_name(
                spelled, self.ctx.registry, self.ctx.module_name)
        return record.qualified_name()

    def _handler_class(self, h: TpyExceptHandler) -> 'RecordInfo | None':
        """The class an except clause names, resolved like any type path
        (`except m.Outer.Err:`). A clause a macro built from a qualified
        name alone resolves by that name."""
        if h.exception_path:
            path = list(h.exception_path)
            ref = self.ctx.type_ref_path(path)
            if ref is not None:
                return ref.info if ref.kind == SymbolKind.RECORD else None
            miss = self.ctx.type_path_miss(path, "an exception class")
            if miss is not None:
                raise self.ctx.error(miss, h)
            return None
        return self.ctx.registry.find_record_by_qname(qualify_exception_name(
            h.exception_type, self.ctx.registry, self.ctx.module_name))

    def _analyze_raise_expr(self, stmt: TpyRaise) -> None:
        """Analyze 'raise <expr>' where expr is a general expression.

        Phase 20 Stage 3: accepts a `Box[Throwable]` (or any other type
        implementing the `Deref` protocol whose peeled type is Throwable)
        in addition to plain Exception subclasses. Codegen lowers via
        `<expr>.__raise__()` which auto-derefs through Box's vtable.
        """
        if not self.ctx.in_function_body():
            raise self.ctx.error(
                "'raise' can only be used inside a function", stmt)
        found = self._raised_operand_class(stmt.raise_expr)
        if found is not None:
            record, spelled = found
            self._raise_class(stmt, record, spelled, stmt.raise_expr)
            return
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
                t_record = self.ctx.registry.get_record_for_type(t)
                if t_record is not None and is_exception_record(t_record, self.ctx.registry):
                    self.init.mark_terminated()
                    return
            raise self.ctx.error(
                f"cannot raise expression of type '{expr_type}'; "
                f"its __deref__ target '{t}' is not Throwable",
                stmt)
        type_name = self._raise_expr_type_name(expr_type, stmt)
        raised_record = self.ctx.registry.get_record_for_type(unwrap_qualifiers(expr_type))
        if raised_record is not None and raised_record.is_return_exception:
            raise self.ctx.error(
                f"'raise <expr>' cannot be used with ReturnException type "
                f"'{type_name}'; use direct 'raise {type_name}' inside "
                f"an @error_return function instead",
                stmt)
        if raised_record is None or not is_exception_record(raised_record, self.ctx.registry):
            raise self.ctx.error(
                f"cannot raise expression of type '{type_name}'; "
                f"it must inherit from Exception",
                stmt)
        self.init.mark_terminated()

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
            record = None if is_cf_catch_all else self._handler_class(h)
            if is_cf_catch_all or (record is not None and record.is_return_exception):
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
                    ret.finally_deferred_name = None
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

    def _analyze_finally_body(self, stmt: TpyTry, before: FlowFacts,
                              try_kills: FactKills | None = None) -> None:
        """Analyze a finally body under all-paths entry facts.

        The finally runs on the normal path AND on any mid-try (or
        mid-handler) exception, so check-elision facts the try/else/handler
        bodies may have killed -- or only established -- must not be assumed
        inside it: its entry is the join of before (the state ahead of
        the try statement) with the bodies' end state, minus their kills.
        After the body, normal-path facts are restored for the code
        following the try statement (they hold whenever control flows past
        it normally), minus whatever the finally body itself killed.

        ``try_kills`` lets callers that already scanned the try body for
        handler entry pass the result in instead of re-scanning.
        """
        if not stmt.finally_body:
            return
        entry_kills = FactKills()
        entry_kills.update(try_kills if try_kills is not None
                           else self.ctx.fact_kills(
                               stmt.try_body))
        if stmt.else_body:
            entry_kills.update(self.ctx.fact_kills(
                stmt.else_body))
        for h in stmt.handlers:
            entry_kills.update(self.ctx.fact_kills(
                h.body))
        normal_narrowed = dict(self.ctx.func.narrowed_types)
        normal_non_null = set(self.ctx.func.non_null_ptr_vars)
        normal_ranges = dict(self.ctx.func.value_ranges)
        self.init.join_entry_from_any_point(before, entry_kills)
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
        self.init.apply_fact_kills(self.ctx.fact_kills(
            stmt.finally_body))
        self.ctx.func.narrowed_types.update(after_narrowed)
        self.ctx.func.non_null_ptr_vars |= after_non_null
        self.ctx.func.value_ranges.update(after_ranges)

    def _analyze_try_finally_only(self, stmt: TpyTry) -> None:
        """Analyze try/finally with no except handlers."""
        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        before = self.init.save()
        for s in stmt.try_body:
            self.analyze_stmt(s)
        try_bindings = dict(self.ctx.func.current_scope.bindings)
        self._analyze_finally_body(stmt, before)
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

        handler_record = None
        if not is_return_exception_catch_all:
            handler_record = self._handler_class(handler)
            if handler_record is None:
                raise self.ctx.error(
                    f"Unknown error type '{handler.exception_type}'", stmt)
            handler.resolved_type = record_owner(handler_record)

        # Set try context so call analysis can allow error_return calls
        prev_try_error = self.ctx.func.try_except_error_type
        prev_handled = self.ctx.func.try_except_error_handled
        self.ctx.func.try_except_error_handled = False
        if is_return_exception_catch_all:
            self.ctx.func.try_except_error_type = "*"
        else:
            self.ctx.func.try_except_error_type = self._exception_qname(
                handler.exception_type, handler_record)

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
        try_kills = self.ctx.fact_kills(stmt.try_body)
        self.init.apply_fact_kills(try_kills)
        self.ctx.func.current_consumed_own_params = consumed_before.copy()

        # Register except binding -- use find_record_by_qname because
        # handler.exception_type may be module-qualified (e.g. from macros).
        prev_ns_binding = None
        ns_bound = False
        binder_scope = self.ctx.func.current_scope
        prev_binder = None
        if handler.binding:
            exc_record = handler_record
            if exc_record:
                exc_type = NominalType(exc_record.name, _module_qname=exc_record.qualified_name())
                self.ctx.func.current_scope.bindings[handler.binding] = exc_type
                prev_binder = register_binder(
                    self.ctx, binder_scope, handler.binding, handler)
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
        if handler.binding and handler_record:
            unregister_binder(binder_scope, handler.binding, prev_binder)
        self._retire_capture_binding(handler.binding, prev_ns_binding, ns_bound)
        else_state = self.init.save()
        consumed_after_else = self.ctx.func.current_consumed_own_params.copy()
        else_terminated = self.ctx.func.init_terminated

        self.init.merge_branches(then_state, else_state)
        self._merge_consumed_own(
            then_terminated, else_terminated,
            consumed_after_then, consumed_after_else)

        # Analyze finally body (runs on all paths, doesn't affect branch merging)
        self._analyze_finally_body(stmt, before, try_kills)
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
                record = self._handler_class(h)
                if not record:
                    raise self.ctx.error(
                        f"Unknown exception type '{h.exception_type}'", stmt)
                if not is_exception_record(record, self.ctx.registry):
                    raise self.ctx.error(
                        f"'{h.exception_type}' is not an exception type; "
                        f"it must inherit from Exception", stmt)
                handler_records.append(record)
                h.exception_type = self._exception_qname(h.exception_type, record)
                h.resolved_type = record_owner(record)
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
        try_kills = self.ctx.fact_kills(stmt.try_body)
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
            binder_scope = self.ctx.func.current_scope
            prev_binder = None
            if h.binding and record is not None:
                exc_type = NominalType(record.name, _module_qname=record.qualified_name())
                self.ctx.func.current_scope.bindings[h.binding] = exc_type
                prev_binder = register_binder(
                    self.ctx, binder_scope, h.binding, h)
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
            if h.binding and record is not None:
                unregister_binder(binder_scope, h.binding, prev_binder)
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
        self._analyze_finally_body(stmt, before, try_kills)
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
                                     iterable_type: TpyType) -> bool:
        """The for-each's iteration loan, and where its loop variable's
        element comes from -- one registration for BOTH for-each routes.
        True when the iteration filed a loan its sources name
        (`loop_var_iterable`)."""
        cur = self.ctx.func.current_function
        frame_loop = isinstance(cur, TpyFunction) and stmt is cur.genexpr_loop
        loan = register_iteration_loans(
            self.ctx, IterHolder(stmt), stmt.iterable, iterable_type,
            self.ctx.func.genexpr_source_loans if frame_loop else ())
        stmt.iter_borrow_unplaceable = loan.unplaceable
        if isinstance(cur, TpyFunction) and cur.is_genexpr and not frame_loop:
            cur.genexpr_inner_loans += tuple(
                (key, info) for key, info in loan.loans
                if key.root in cur.capture_params
                and (key, info) not in cur.genexpr_inner_loans)
        self.ctx.record_loop_sources(
            stmt, stmt.iterable, loan.sources,
            loan.elem_sources if stmt.is_tuple_unpack else None)
        return bool(loan.sources)

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
            _, aiter_info = self.type_ops.bind_inherited_method(
                aiter_info, iterable_inner)
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
        stmt.async_aiter_type, anext_info = self.type_ops.bind_inherited_method(
            anext_info, aiter_type)
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
        self._note_loop_var_iteration(stmt.var, elem_type, False,
                                      placeable=False)
        stmt.elem_type = make_ref(elem_type)
        # What each step hands over is what `__anext__`'s declaration writes.
        anext_decl = anext_overloads[0]
        self._stamp_step_handover(stmt, elem_type,
                                  declared=anext_decl.async_inner_return)
        self._check_loop_var_rebind(stmt, elem_type)
        cid_floor = self.ctx.pending_num_counter
        var_type, declared = self._loop_var_cell_type(stmt, elem_type)
        self._declare_loop_var(stmt, declared)

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
                before, kills=self.ctx.fact_kills(
                    stmt.body))
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
            with self.scopes.loop_var(inner_scope, stmt.var, var_type,
                                       inner_scope.depth, is_foreach=True,
                                       binder=stmt):
                for s in stmt.body:
                    self.analyze_stmt(s)
            self.pend.retire_loop_heads(cid_floor)
        self._finish_loop_clauses(
            stmt, inner_scope, edges, before=before, pre_loop=before,
            consumed_before=consumed_before_loop,
            ns_types_before=ns_types_before, runs_once=runs_once,
            elem_type=unwrap_ref_type(stmt.elem_type))

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
        if is_rvalue_source(self.ctx, manager):
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

        # A suppressing `__exit__` resumes after the statement from any
        # point its manager covers: from the first item's `__enter__` on.
        before: FlowFacts | None = None
        pushed: list[tuple[str, Scope, object]] = []
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
            # The exit step runs TPy code unless the manager is native: a
            # call on the manager, on every path out of the block.
            item.exit_runs_code = self.ctx.manager_exit_runs_code(ctx_type)
            if item.exit_runs_code:
                item.enter_call = TpyMethodCall(
                    obj=item.context_expr, method=enter_method, args=[],
                    resolved_function_info=enter_info, loc=item.loc)
                item.exit_call = TpyMethodCall(
                    obj=item.context_expr, method=exit_method, args=[],
                    resolved_function_info=exit_info, loc=item.loc)

            # An inherited __[a]enter__/__[a]exit__ from a generic base reports
            # the base's unbound T; bind it so the `as` target / exit-suppress
            # type is concrete and codegen can name the async sub-coro struct.
            enter_owner = exit_owner = None
            if isinstance(ctx_type, NominalType):
                enter_owner, enter_info = self.type_ops.bind_inherited_method(
                    enter_info, ctx_type)
                exit_owner, exit_info = self.type_ops.bind_inherited_method(
                    exit_info, ctx_type)

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
                if self.ctx.func.current_scope.lookup(item.target) is None:
                    self.ctx.declare_local(
                        item.target, self.ctx.func.current_scope.depth)
                resolved = self._infer_new_local_type(
                    item.target, enter_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                self.ctx.func.with_target_names.add(item.target)
                item.enter_type = resolved
                self.ctx.func.current_scope.define(item.target, resolved)
                pushed.append((item.target, self.ctx.func.current_scope,
                               register_binder(
                                   self.ctx, self.ctx.func.current_scope,
                                   item.target, item)))
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
                    record_borrow_binding(
                        self.ctx, item.target,
                        const=declared_result_readonly(enter_info),
                        at=item.context_expr)
                    self.ctx.func.nonstmt_borrow_bindings.add(item.target)
                elif stmt.is_async and not resolved.is_value_type():
                    # The awaited `__aenter__` result lends as the sync one
                    # does; only its const verdict is the table's to skip.
                    note_nonowning_bind(self.ctx, item.target,
                                        item.context_expr)

            # A header's proof would go live only at the statement boundary,
            # past the `__enter__` code that runs in between: drop it.
            self.ctx.func.pending_non_null_ptr_vars.clear()
            if item.enter_call is not None:
                self.narrowing.invalidate_for_code_run(item.enter_call)
            if stmt.is_async:
                # This item's `__aenter__` awaits before the next item's
                # context expression (or the body) runs.
                self.narrowing.invalidate_suspension_facts()
            if before is None:
                before = self.init.save()

        # Analyze the body -- track new variable declarations so codegen
        # can pre-declare them outside the guard {} scope (C++ scoping).
        scope_before = set(self.ctx.func.current_scope.bindings.keys())

        for s in stmt.body:
            self.analyze_stmt(s)
        if before is not None and any(
                item.exit_can_suppress for item in stmt.items):
            # A suppressing `__exit__` resumes after the statement from
            # wherever its body raised, as a handler does: only facts that
            # held once the first manager was entered and that nothing after
            # it kills. The exit calls kill on every path, so they run after.
            kills = self.ctx.fact_kills(
                stmt.body,
                extra_exprs=[item.context_expr for item in stmt.items[1:]])
            kills.names.update(item.target for item in stmt.items[1:]
                               if item.target is not None)
            self.init.join_entry_from_any_point(before, kills)
        for item in reversed(stmt.items):
            if item.exit_call is not None:
                self.narrowing.invalidate_for_code_run(item.exit_call)
        for name, scope, prev in reversed(pushed):
            unregister_binder(scope, name, prev)
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
        *,
        foreign_globals: set[str] = frozenset(),
        nonlocal_params: frozenset[str] = frozenset(),
    ) -> ScanResult:
        """Shared body analysis: bind params, prescan, analyze statements.
        Callers handle scope creation, type resolution, and post-processing.
        `foreign_globals` names the module globals another body rebinds
        through `global`. `nonlocal_params` are params that stand for the
        enclosing scope's variables (a genexpr's written captures), which the
        caller binds as `nonlocal` names.
        """
        module_init = self.ctx.is_top_level
        # Bind params to scope with Ref for non-value types.
        # analyze_expr strips Ref from return values, so downstream sema
        # sees bare types.  Ref on scope types enables provenance-aware
        # code (assignment warnings, local scope propagation).
        param_names: set[str] = set()
        for pname, ptype in params:
            if pname in nonlocal_params:
                continue
            param_names.add(pname)
            scope_type = make_ref(ptype)
            scope.define(pname, scope_type)
            self.ctx.declare_local(pname, scope.depth, block_depth=0)
            self.ctx.func.definitely_assigned.add(pname)
            if ns:
                ns.bind_variable(pname, scope_type)

        # The body's copy diagnostics wait for its settlement.
        self.ctx.func.copies_from = len(self.ctx.diagnostics)
        # Param tracking for mutation analysis
        self.ctx.func.current_param_names = param_names
        self.ctx.func.current_param_name_to_idx = {
            p: i for i, (p, _) in enumerate(params) if p not in nonlocal_params}
        # For non-static methods, include "self" in the param index map with
        # sentinel -1 so that _record_mutation_call_edges can track self
        # flowing through free function calls (e.g., helper(self)).
        # Phase 2 (_resolve_single) converts caller_idx == -1 to self_mutated.
        if func.is_method and not func.is_staticmethod:
            self.ctx.func.current_param_name_to_idx["self"] = -1

        # Prescan for reassigned variables + last-use liveness
        scan = scan_reassigned_vars(func.body,
                                    pre_declared=param_names | nonlocal_params,
                                    pending_nums=True)
        assert scan.literal_locals is not None
        self.ctx.func.pending_num_names = scan.literal_locals.pending
        self.ctx.func.int_literal_values = scan.literal_locals.literals
        self.ctx.func.pending_num_excluded = scan.literal_locals.excluded
        self.ctx.func.pending_num_rebound = scan.literal_locals.rebound
        self.ctx.func.first_bindings = scan.first_bindings
        # Python binds a nested def's name for the whole enclosing scope, so
        # the set has to exist before the walk reaches any `def`. The update
        # (not an assign) keeps the outer scope's still-unbound names a
        # nested def was seeded with.
        self.ctx.func.nested_def_pending.update(scan.nested_def_bind_loc)
        self.ctx.func.own_ns = ns
        self._warn_scalar_type_shadows(None if module_init else func,
                                       param_names, scan)
        self.ctx.func.closure_pinned = closure_pinned_names(func.body)
        self.ctx.all_last_uses |= analyze_last_uses(
            func.body, liveness_alias_sources(scan),
            pinned=self.ctx.func.closure_pinned,
            captured=(nested_def_free_names(func) if func.is_nested_def
                      else frozenset()),
            same_stmt_alias_reads=self.ctx.same_stmt_alias_reads,
            repeated_reads=self.ctx.repeated_reads)
        self.ctx.finally_return_candidates |= collect_finally_return_candidates(func.body)
        self.ctx.finally_rebound_returns |= collect_finally_rebound_returns(func.body)
        self.ctx.func.current_reassigned_vars = scan.reassigned.copy()
        self.ctx.func.body_bound_names = frozenset(
            scan.bound_names() | scan.reassigned)
        self.ctx.func.nested_nonlocal_rebinds = (
            collect_nested_def_nonlocal_rebinds(func.body, include_del=True)
            | self.ctx.func.enclosing_nonlocal_rebinds)
        self.ctx.func.deleted_names = collect_deleted_names(func.body)
        self.ctx.func.in_place_writes = collect_in_place_writes(
            func.body, self.ctx.write_summaries)
        self.ctx.func.current_fresh_ctor_locals = set()
        self.ctx.func.tuple_unpack_view_targets = set()
        self.ctx.func.current_aug_assigned_vars = scan.aug_assigned.copy()
        self.ctx.func.current_alias_sources = dict(scan.alias_sources)
        self.ctx.func.current_chain_alias_sources = dict(scan.chain_alias_sources)

        # Analyze body
        for stmt in func.body:
            if module_init:
                # A parser-minted comprehension temp is an init-scope local,
                # not a module slot; every other depth-0 binding declares one.
                self.ctx.current_module_stmt = (
                    None if isinstance(stmt, TpyVarDecl) and stmt.module_init_local
                    else stmt)
            self.analyze_stmt(stmt)
        if module_init:
            self.ctx.current_module_stmt = None

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
                            | self.ctx.func.global_declarations
                            | foreign_globals),
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

    def _replay_deferred_write(self, mark: NestedMutationMark) -> None:
        """A nested def's write that happens only if `mark.callee` writes
        through its slot, as a call edge on this function: on its receiver,
        or on whatever the captured name reaches here -- the loop bindings
        it climbs through and the params past them (`write_reach`, the climb
        this body's own calls take: a member path or a borrow as it is, a
        loop binding element-stepped), everything element-stepped where the
        nested def reached the capture through a loop binding."""
        assert mark.callee is not None
        callee = mark.callee.root
        if mark.receiver:
            idx = self.ctx.self_receiver_index()
            sites: tuple[object, ...] = ()
            targets = ([(idx, mark.via_element, False)] if idx is not None
                       else [])
        else:
            reach = self.ctx.write_reach(
                mark.key.as_name(), stepped=mark.via_element,
                handles=mark.slot >= 0)
            sites = reach.bindings
            targets = [(i, elem, lent or mark.lent)
                       for i, elem, lent in reach.params]
        record_receiver_edges(self.ctx, callee, targets, sites,
                              slot=mark.slot)

    def _analyze_nested_def(self, stmt: TpyNestedDef) -> None:
        """Analyze a nested function definition."""
        func = stmt.func

        if self.ctx.func.in_nested_def:
            raise self.ctx.error(
                "Nested functions cannot contain further nested functions",
                stmt)

        # The body's `nonlocal` declarations, bound in the inner scope before
        # its analysis.
        nonlocal_names: set[str] = set()
        collect_nonlocals(func.body, nonlocal_names)
        # The body is analyzed under a function state of its own: the
        # pending locals it reads or stores through `nonlocal` get their type
        # here, in this function. A name it binds itself only shadows one.
        self.pend.settle_names(
            _nested_def_free_names(func, into_lambdas=True) - nonlocal_names,
            stmt,
            f"read by the nested function '{func.name}'",
            container_what=f"captured by the nested function '{func.name}'")
        self.pend.settle_names(
            nonlocal_names, stmt,
            f"declared nonlocal in the nested function '{func.name}'")
        # Collect outer locals available for capture
        outer_locals = self.ctx.func.definitely_assigned.copy()
        # Sibling nested defs the enclosing scope has not bound yet: this
        # body shares the enclosing scope's binding for their names, so a
        # read of one must not resolve outward either. The def's OWN name is
        # excluded -- a self-reference is the recursion diagnostic instead.
        outer_pending = {n: loc for n, loc
                         in self.ctx.func.nested_def_pending.items()
                         if n != func.name and n not in outer_locals}

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
        enclosing = enclosing_scope_facts(self.ctx, nonlocal_names)

        # Analyze body in isolated scope
        with self.scopes.nested_def_scope(func) as inner_scope:
            enter_enclosing_scope(self.ctx, enclosing)
            self.ctx.func.nested_def_pending = outer_pending
            bind_nonlocals(self.ctx, enclosing, inner_scope, nonlocal_names,
                           declare=False)

            self._prescan_and_analyze_body(func, params, inner_scope, self.ctx.func.current_ns)

            # The nested def has its own tracking state, which the scope exit
            # discards -- the deductions it accumulated have to be resolved
            # and the checks it deferred have to run before that, or they
            # never run at all. Same order as a top-level body: the drain
            # reads the storage the resolution settles.
            self.deduction.resolve_all()
            self.compat.drain_deferred_escape_checks()
            finalize_pointer_tuples(self.ctx, func, nested=True,
                                    compat=self.compat)
            # The def's own lambdas read its function state, which the
            # scope exit discards.
            warn_closure_captures(self.ctx, func.body)
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
            # them: attempted mutation marks (filtered to outer names below),
            # the writes deferred to a callee among them, and self-receiver
            # call edges as such (their param_map indexes the nested def's
            # own params, so only the index-free self fact is portable).
            nested_marks = list(self.ctx.func.nested_mutation_marks) + [
                NestedMutationMark(StorageKey.of_name("self"),
                                   through_field=False, structural=False,
                                   via_element=False, receiver=True,
                                   callee=e.callee_fi)
                for e in self.ctx.func.current_call_edges
                if e.receiver_idx == -1]
        stmt.nonlocal_names = nonlocal_names
        # After the scope restore: any later call in the enclosing function
        # may invoke this closure, killing facts for its nonlocal targets and
        # for the outer storage it writes through.
        self.ctx.func.closure_written_names |= nonlocal_names
        exports = closure_exports(stmt, self.ctx.write_summaries)
        paths = self.ctx.func.closure_mutated_paths
        for path, stores in exports.paths.items():
            keeps = self.narrowing.stores_keep(stores)
            paths[path] = keeps if paths.get(path, keeps) == keeps else None
        self.ctx.func.closure_mutated_receivers |= exports.receivers

        # Compute captures: free variables that come from outer scope
        captured = sorted(
            (_nested_def_free_names(func) - nonlocal_names) & outer_locals
        )
        # Nonlocal names are also captures (mutable references)
        for name in sorted(nonlocal_names):
            if name not in captured:
                captured.append(name)
        stmt.captured_names = captured

        # Replay the closure's mutation facts into the enclosing state:
        # defining the closure conservatively counts as performing its
        # mutations (the def-site stance closure_written_names already
        # takes). Only names reaching outer storage are replayed -- a capture,
        # a nonlocal, or the receiver, as the mark recorded it; the nested
        # def's own params/locals are filtered out.
        # A deferred write names only outer storage already (`write_reach`
        # reaches a capture through a loop binding, not as a free name).
        replayable = set(captured) | nonlocal_names
        for mark in nested_marks:
            if (mark.callee is None and not mark.receiver
                    and mark.key.as_name() not in replayable):
                continue
            if mark.callee is not None:
                self._replay_deferred_write(mark)
            elif mark.structural:
                self.ctx.mark_param_structurally_mutated(mark.key)
            else:
                self.ctx.mark_param_mutated(mark.key,
                                            through_field=mark.through_field,
                                            via_element=mark.via_element)

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
        stmt.frame_type = frame_type_of_function(fi)

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
        """
        if not stmt.loc or stmt.is_tuple_unpack:
            return
        key = (stmt.loc.line, stmt.var)
        table = self.ctx.declared_var_types
        recorded = resolve_int_literals(elem_type,
                                        self.ctx.default_int_for_literal)
        table[key] = recorded
        if value_leaves(recorded):
            # An element of a container whose cells decide its leaves
            # follows them, as a declaration bound to one does.
            def follow(_types: tuple[TpyType, ...]) -> None:
                table[key] = self.pend.finalize_settled(table[key])
            self.pend.defer(stmt, (recorded,), follow)
        elif self.pend.container_cells(recorded) is not None:
            # A row of a container whose cells decide it (`for row in g`)
            # follows its resolution, as `row = g[0]` does.
            def resolve(resolved: TpyType) -> None:
                table[key] = resolved
            self.ctx.func.after_list_resolution.append((recorded, resolve))

    def _declare_loop_var(self, stmt: TpyForEach, declared: TpyType) -> None:
        """Declare the variable a `for` head binds anew at the type its
        bindings decide (`derived_binding`); a pending one is decided
        with its function. A head assigning an existing local declares
        nothing."""
        if not stmt.hoist_loop_var:
            stmt.elem_type = make_ref(declared)
        self._record_for_loop_var_type(
            stmt, unwrap_ref_type(stmt.elem_type))
        if contains_pending_leaf(stmt.elem_type):
            self.ctx.func.pending_elem_type_fields.append((stmt, "elem_type"))

    def _loop_var_cell_type(self, stmt: TpyForEach,
                            elem_type: TpyType) -> tuple[TpyType, TpyType]:
        """The types the loop variable reads as in the body and is declared
        at (`derived_binding`). A head binding
        an element a cell has not decided yet is a local first bound to a
        value over a pending one (`y = xs[0]`): it gets a derived cell, so
        a store into it, a capture of it or a use that needs its type
        works on its own cell, never widening the container's."""
        if stmt.hoist_loop_var:
            # The head assigns the existing local, which reads as its own
            # type; `_check_loop_var_rebind` holds the element to it, or
            # joins it into the local's cell.
            existing = self.ctx.func.current_scope.lookup(stmt.var)
            if existing is None:
                return elem_type, elem_type
            existing = unwrap_readonly(unwrap_ref_type(existing))
            return existing, existing
        return self.pend.derived_binding(stmt.var, elem_type, stmt,
                                         loop_head=True)

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

    def _stamp_step_handover(self, stmt: TpyForEach, step: TpyType, *,
                             declared: TpyType | None = None) -> None:
        """Stamp what each step hands over (`step_handover_paths`) on the
        loop and its unpack head, before the body."""
        stmt.step_handover = step_handover_paths(
            self.ctx, stmt.iterable, step, declared=declared)
        head = stmt.head_unpack()
        if head is not None:
            head.step_handover = stmt.step_handover

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
        fam = view_family_for_type(exist_bare)
        if (fam is not None and fam is view_family_for_type(elem_bare)
                and (isinstance(exist_bare, PendingViewType)
                     or exist_bare == fam.owned_type)
                and (isinstance(elem_bare, PendingViewType)
                     or elem_bare == fam.owned_type)):
            # One str/bytes local, and the element is one more binding of it:
            # a view local takes the element's storage verdict (the hoist
            # rule's roots, and the source writes that make the element own)
            # through the alias edge, an owned element owns it, and an owned
            # local copies whatever the element is.
            if isinstance(exist_bare, PendingViewType):
                if isinstance(elem_bare, PendingViewType):
                    self.deduction.add_view_source(
                        fam, exist_bare.var_id, elem_bare.var_id)
                else:
                    self.deduction.mark_view_reassigned_from_owned(
                        stmt.var, fam, var_id=exist_bare.var_id)
            exist_bare = elem_bare = fam.owned_type
        stmt.hoist_loop_var = True
        if self.pend.rebind(stmt.var, elem_bare, None, stmt) is not None:
            # A local whose bindings join takes the element as one more.
            return

        def check(types: tuple[TpyType, ...]) -> None:
            exist_t, elem_t = types
            if exist_t != elem_t:
                raise self.ctx.error(
                    f"for-loop rebinds existing variable '{stmt.var}' of "
                    f"type '{exist_t}' with elements of type "
                    f"'{elem_t}'; rename the loop variable or match "
                    f"the types", stmt)
        if value_leaves(exist_bare) or value_leaves(elem_bare):
            # An element whose container's cells are still open is judged
            # at the type they settle to, as a store of one into a typed
            # local is.
            self.pend.defer(stmt, (exist_bare, elem_bare), check)
        else:
            check((exist_bare, elem_bare))

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
        if (self.ctx.is_top_level
                or self.ctx.func.current_function is CLASS_CONSTANT_CONTEXT):
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

    def final_is_runtime(self, typ: TpyType, init: TpyExpr,
                         class_scope: Mapping[str, bool] | None = None,
                         ) -> bool:
        """Whether a Final of type `typ` runs its compile-time-constant
        initializer at program start rather than as a C++ constant
        expression: its type has no constexpr form, or the initializer
        computes an `int` (BigInt) value on the way (an 8/16-bit operation
        under the BigInt default int), or reads a Final that is runtime
        itself. `class_scope` holds the earlier constants of the class body
        being analyzed, with their facts; they shadow module Finals."""
        return (not is_constexpr_eligible(typ)
                or self._init_is_runtime(init, class_scope or {}))

    def _init_is_runtime(self, init: TpyExpr,
                         class_scope: Mapping[str, bool]) -> bool:
        macro_exp = getattr(init, "macro_expansion", None)
        if macro_exp is not None:
            return self._init_is_runtime(macro_exp, class_scope)
        t = self.ctx.get_expr_type(init)
        if t is not None and is_big_int_type(unwrap_readonly(t)):
            return True
        if self._reads_runtime_final(init, class_scope):
            return True
        return any(self._init_is_runtime(c, class_scope)
                   for c in init.children())

    def _reads_runtime_final(self, node: TpyExpr,
                             class_scope: Mapping[str, bool]) -> bool:
        """Whether `node` reads a Final whose initializer runs at program
        start: a class constant (its record's fact), a module Final of this
        module, or another module's (its export's fact)."""
        if isinstance(node, TpyName):
            if node.name in class_scope:
                return class_scope[node.name]
            if node.name in self.ctx.final_globals:
                return node.name in self.ctx.runtime_finals
            imp = lookup_imported(self.ctx.module_attributes, node.name,
                                  SymbolKind.VARIABLE)
            var = (self.ctx.registry.module_variable(*imp)
                   if imp is not None else None)
            return var is not None and var.runtime_final
        if isinstance(node, TpyFieldAccess):
            owner = node.class_constant_owner
            if owner is not None:
                return node.field in owner.runtime_class_constants
            module_name = self.ctx.module_named_by(node.obj)
            module_info = (self.ctx.registry.get_module(module_name)
                           if module_name is not None else None)
            var = (module_info.variables.get(node.field)
                   if module_info is not None else None)
            return var is not None and var.runtime_final
        return False

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
        """Error if reassigning a non-value-type param, loop variable, or
        global -- the enclosing function's param or loop variable, for a
        `nonlocal` name."""
        fs = self.ctx.func
        existing_type = fs.current_scope.lookup(name)
        if existing_type is None:
            return
        if (name in fs.global_declarations
                and global_binds_by_reference(existing_type)):
            raise self.ctx.error(
                global_rebind_message(name, existing_type), node)
        if unwrap_readonly(existing_type).is_value_type():
            return
        is_nonlocal = name in fs.current_nonlocal_names
        loop_vars = fs.enclosing_loop_vars if is_nonlocal else fs.loop_vars
        params = (fs.enclosing_nonvalue_params if is_nonlocal
                  else nonvalue_params(fs.current_function))
        if name in params:
            raise self.ctx.error(
                f"Cannot reassign parameter '{name}' of type "
                f"'{params[name]}'; assign to a new local variable instead",
                node)
        if name in loop_vars:
            raise self.ctx.error(
                f"Cannot reassign loop variable '{name}' of type '{existing_type}'; "
                f"assign to a new local variable instead",
                node
            )

    def _check_fallback_rebinds_owner(self, stmt: TpyAugAssign,
                                      method: str,
                                      target_type: TpyType) -> str | None:
        """Error on `x += v` resolved to `__add__` when local `x` refers to
        storage it does not own: the update is emitted in place, so it would
        write through to that storage where CPython rebinds `x`. On an
        owned reference-type `x`, return the warning to give once the
        statement's other checks pass: an alias sees the update. Interim:
        BUGS.md#borrow-unpack-target-rebind-writes-through tracks lowering
        the fallback as the assignment `x = x + v`, which lifts this."""
        name = stmt.target.name
        fs = self.ctx.func
        # Module-level statements have no ownership fact here; their
        # fallback aug-assigns do not lower at all.
        if not self.ctx.in_function_body():
            return None
        is_nonlocal = name in fs.current_nonlocal_names
        if (name not in fs.enclosing_borrowing_names
                if is_nonlocal else fs.owns_whole(name)):
            # An owned reference object is updated in place where Python
            # rebinds the name, so an alias sees the change
            # (BUGS.md#borrow-unpack-target-rebind-writes-through).
            if not is_reference_local(target_type):
                return None
            op, imethod, hint = self._fallback_rebind_spelling(
                stmt, is_nonlocal)
            return (f"'{op}=' on '{name}' falls back to '{method}' "
                    f"({user_type_name(unwrap_readonly(target_type))} has "
                    f"no '{imethod}'): the object is updated in place, "
                    f"where Python rebinds '{name}' -- define '{imethod}', "
                    f"or {hint}")
        op, imethod, hint = self._fallback_rebind_spelling(stmt, is_nonlocal)
        raise self.ctx.error(
            f"'{op}=' on '{name}' would rebind '{name}' "
            f"({user_type_name(unwrap_readonly(target_type))} has no "
            f"'{imethod}', so it falls back to '{method}'), but '{name}' "
            f"refers to an object it does not own; this is not supported "
            f"yet -- {hint}",
            stmt,
        )

    @staticmethod
    def _fallback_rebind_spelling(stmt: TpyAugAssign,
                                  is_nonlocal: bool) -> tuple[str, str, str]:
        """The operator, the in-place dunder Python would look up, and the
        rebind spelling to suggest for a fallback `x op= v`."""
        name = stmt.target.name
        op = op_spelling(stmt.op)
        # `**` has no in-place dunder in the table; name the one Python
        # would look up all the same.
        imethod = (builtin_modules.AUGOP_TO_IMETHOD.get(stmt.op)
                   or "__i" + builtin_modules.BINOP_TO_METHOD[stmt.op][2:])
        value = expression_spelling(stmt.value) or "..."
        # Through `nonlocal`, the plain rebind `w = w + v` writes into the
        # borrowed object too (BUGS.md#nonlocal-rebind-overwrites-in-place),
        # so steer to a fresh local instead.
        hint = (f"bind the result to a new name, e.g. "
                f"'{name}2 = {name} {op} {value}'" if is_nonlocal
                else f"write '{name} = {name} {op} {value}'")
        return op, imethod, hint

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
            # The pending leaves answer through the alias pass; every other
            # leaf (`x = a or h.s`) answers the view rule here.
            other_leaf_owns = init_expr is not None and not all(
                isinstance(self.ctx.get_expr_type(leaf.origin),
                           PendingViewType)
                or self.deduction.leaf_lends_view(leaf)
                for leaf in view_leaves(init_expr, ViewLend(self.ctx)))
            info = ViewVarInfo(var_id=var_id, variable_name=name,
                               decl_line=line, source_var_ids=source_ids,
                               initialized_from_owned=other_leaf_owns,
                               frame_unsafe_source=(
                                   init_expr is not None
                                   and self.deduction.has_nonstatic_view_source(init_expr)))
        else:
            # Fresh from owned type (str or bytes)
            if init_expr is None:
                is_owned = False
            elif is_borrowing_view_type(var_type):
                # The source's type is already the view, but whether the
                # local may keep it is the same rule an owned-typed source
                # answers: a view of a field or a container element owns.
                is_owned = (view_source_is_temporary(self.ctx, init_expr)
                    or not self.deduction.is_view_compatible_source(init_expr, var_type))
            else:
                is_owned = not self.deduction.is_view_compatible_source(init_expr, init_type)
            info = ViewVarInfo(var_id=var_id, variable_name=name,
                               decl_line=line, initialized_from_owned=is_owned,
                               frame_unsafe_source=(
                                   not is_owned
                                   and self.deduction.has_nonstatic_view_source(init_expr)))
            if is_owned and init_expr is not None:
                src = peel_coerce(init_expr)
                if (isinstance(src, (TpyFieldAccess, TpySubscript))
                        and not view_source_is_temporary(self.ctx, src)):
                    info.owns_stored_source = True
                    info.stored_source = storage_spelling(src)

        info.module_body = self.ctx.is_top_level
        vars_reg[var_id] = info
        var_map[name] = var_id
        self.ctx.func.view_ids_by_name.setdefault(name, []).append((family, var_id))
        # No expression here means the binding site records it itself (a
        # loop variable, an unpack target); until then it is unknown.
        self.deduction.note_view_binding(family, var_id, init_expr)
        self.ctx.view_pending_resolutions(family).append(var_id)
        return family.pending_type_class(var_id)

    def _infer_new_local_type(
        self, name: str, var_type: TpyType,
        init_expr: TpyExpr | None, init_type: TpyType | None,
        line: int | None, *, annotated: bool = False,
    ) -> TpyType:
        """Apply deferred type inference for a new local variable.

        Handles view-type families (str/bytes -> PendingViewType) and
        PendingListType alias logic.

        When init_expr is None (for-loop var, tuple unpack), the source is
        considered view-compatible (initialized_from_owned=False) because
        the container outlives the loop/unpack scope -- in a SYNC body; a
        resumable frame outlives its case-block temps, so these register
        as frame-unsafe and resolve owned there (frame_unsafe_source).
        """
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
            pending = self._infer_new_local_view_type(
                name, var_type, init_expr, init_type, line, family)
            return var_type if isinstance(var_type, LiteralType) else pending
        elif (isinstance(var_type, PendingListType)
                and init_expr is not None and isinstance(init_expr, TpyName)):
            return self.deduction.register_list_alias(
                name, var_type,
                decl_line=line,
            )

        return var_type

    def _note_loop_var_iteration(self, var: str, elem_type: TpyType,
                                 iterates: bool, *,
                                 placeable: bool = True) -> None:
        """Record the storage this loop's variable iterates -- only what THIS
        loop registered (`iterates`; a reused name keeps the last loop's
        entry otherwise) -- and, for a str/bytes loop variable, its one
        binding."""
        keys = (self.ctx.loop_var_sources(var)
                if placeable and iterates else None)
        roots = {k.root for k in keys} if keys else None
        self.ctx.func.loop_var_iter_roots[var] = roots
        inner = unwrap_own(unwrap_ref_type(unwrap_readonly(elem_type)))
        if isinstance(inner, PendingViewType):
            self.deduction.mark_view_non_lending(inner.family, inner.var_id)
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

    def _consuming_loop_revert(self, stmt: TpyForEach, iterable_type: TpyType,
                               elem_type: TpyType) -> 'Callable[[], None]':
        """What turns a consuming loop back into a borrowing one once the
        ownership fact calls its iterable non-owning (`settle_owning_reads`):
        its elements are copied after all."""
        def revert() -> None:
            stmt.consuming_iter_fi = None
            self._decide_loop_copies_var(stmt, iterable_type, elem_type)
        return revert

    def _decide_loop_copies_var(self, stmt: TpyForEach,
                                iterable_type: TpyType,
                                elem_type: TpyType) -> None:
        """The loop variable's copy verdict, over the body and `else`
        clause once they are analyzed. A variable the loop may write the
        source of holds its own copy: the planner binds a value element by
        value, and a str / bytes view owns."""
        decide_copies_var(self.ctx, stmt, stmt.iterable, iterable_type,
                          lambda: for_live_region(stmt), elem_type,
                          handed_over=stmt.consuming_iter_fi is not None)

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
        added = {stmt.var, *stmt.head_targets()}
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
            with self.pend.sink(stmt.value if is_numeric_slot(elem_type)
                                else None):
                yield_type = self.expr.analyze_at_slot(
                    stmt.value, elem_type, CoercionContext.RETURN)
        stmt.value = self.compat.coerce_expr(
            stmt.value, yield_type, elem_type, "yield value",
            coercion_ctx=CoercionContext.RETURN)
        # The coercion compares the literal against the declared tuple as a
        # whole, so an `Own` element's scalar `T -> Own[T]` copy rule never
        # runs per member -- apply it here.
        yield_slot = unwrap_ref_type(elem_type)
        # An owned element is an owning sink whatever the source.
        self.compat.register_owned_result(stmt.value, yield_slot,
                                          TupleSink.YIELD)
        self.compat.check_tuple_result(stmt.value, yield_slot,
                                       TupleSink.YIELD)
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
        # A borrow yield hands the consumer a reference into the frame's
        # source storage (a self field, a param, a borrow tuple's element
        # pointers), as returning one does: a mutable one keeps the source
        # writable, so an enclosing method is not auto-inferred readonly and
        # the mutable val_or_ref<T> slot can bind it. An Own[T] yield moves.
        self.ctx.escape_marks(stmt.value, unwrap_ref_type(elem_type),
                              position=EscapePosition.YIELD)
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

    def _store_pending_num(self, cell: PendingNumCell, value_expr: TpyExpr,
                           value_type: TpyType | None, site: TpyStmt,
                           ) -> tuple[TpyType, TpyExpr]:
        """Record a store of `value_expr` into a pending local, its value
        converted once the local's type is decided. Returns what the local
        reads as now and the value to store."""
        var_type = self.pend.record_binding(cell, value_type, value_expr, site)
        if isinstance(strip_int(value_type), (IntLiteralType, FloatLiteralType)):
            note_elem_binding(self.ctx, value_expr)
        else:
            value_expr = self.compat.coerce_expr(
                value_expr, strip_int(value_type), var_type,
                f"variable '{cell.name}'",
                coercion_ctx=CoercionContext.ASSIGN)
        return var_type, value_expr

    def _indexed_list(self, target: TpyExpr) -> TpyType | None:
        """The list a store through `target` writes one element of."""
        if (not isinstance(target, TpySubscript)
                or isinstance(target.index, TpySlice)):
            return None
        return self.ctx.get_raw_expr_type(target.obj)

    def _target_elem_cell(self, target: TpyExpr) -> PendingNumCell | None:
        """The cell an augmented store through `target` writes to: the
        element cell of the list literal `target` indexes, the value cell
        of the dict -- the container's last part. The update is the
        operator's result, settled with both its sides, which the call's
        container preparation cannot see."""
        lc = self.pend.container_cells(self._indexed_list(target))
        if lc is None or isinstance(lc.tree, PendingSetType):
            return None
        leaf = at_path(lc.tree, lc.tree.STEPS[-1:])
        return (self.ctx.pending_num_cells.get(min(leaf.cells))
                if isinstance(leaf, PendingNumType) else None)

    def _bind_list_elem_cell(self, name: str, site: TpyStmt,
                             value: TpyExpr | None,
                             value_type: TpyType | None,
                             existing_type: TpyType | None) -> TpyType | None:
        """A non-empty container literal -- a list, a dict, a set -- whose
        leaves hold numbers (scalars, tuple members, the elements of nested
        rows, a dict's keys and values) bound to unannotated function local
        `name`: from here on each numeric leaf is decided by a cell every
        use of the container refers to (`PendingNums.new_tree`), and the
        literal's entries are the first it holds; a literal that rebinds a
        container with cells joins its cells (`PendingNums.bind_container`).
        Returns the value's type, naming the cells."""
        if self.ctx.trial_depth:
            return value_type
        # A comprehension of rows has one row record, its element's: the
        # rows it builds are that one written many times.
        rows_comp = (isinstance(value, TpyListComprehension)
                     and isinstance(value_type, PendingListType)
                     and pending_list_of(value_type.element_type) is not None)
        info = self._written_container_record(name, site, value, value_type)
        if info is None:
            return value_type
        root = info.pending_type()
        existing = (self.pend.cell_container(existing_type)
                    if existing_type is not None else None)
        if existing_type is not None and (
                existing is None or type(root_of(existing)) is not type(root)):
            return value_type
        if rows_comp:
            entries = [Entry(((value_type.element_type, None),))]
        else:
            entries = []
            for exprs in literal_entries(value, root) or ():
                types = [self.ctx.get_expr_type(e) for e in exprs]
                if any(t is None for t in types):
                    return value_type
                entries.append(Entry(tuple(zip(types, exprs))))
        lc = self.pend.bind_container(
            existing, info, name,
            site if isinstance(site, TpyVarDecl) else None,
            entries, unwrap_qualifiers(value_type), site)
        if lc is None:
            return value_type
        if info.literal_id not in self.ctx.container_literals:
            # A written dict or set has no record of its own until here.
            self.ctx.container_literals[info.literal_id] = info
            self.ctx.func.pending_resolutions.append(info.literal_id)
        bound = info.pending_type()
        self.ctx.set_expr_type(value, bound)
        return bound

    def _written_container_record(
            self, name: str, site: TpyStmt, value: TpyExpr | None,
            value_type: TpyType | None) -> ContainerLiteralInfo | None:
        """The record a written container literal bound to `name` decides
        its leaves through: a list literal's own (made at its analysis), a
        new one for a written dict or set. None for any other value, or a
        literal whose record takes no cell."""
        if isinstance(value, (TpyDictLiteral, TpySetLiteral)):
            written = (value.keys if isinstance(value, TpyDictLiteral)
                       else value.elements)
            cls = (PendingDictType if isinstance(value, TpyDictLiteral)
                   else PendingSetType)
            if not written or not pairs_as(cls, unwrap_qualifiers(value_type)):
                return None
            literal_id = self.ctx.literal_counter
            self.ctx.literal_counter += 1
            return cls.new_record(
                literal_id, value, variable_name=name,
                decl_line=site.loc.line if site.loc else None)
        rows_comp = (isinstance(value, TpyListComprehension)
                     and isinstance(value_type, PendingListType)
                     and pending_list_of(value_type.element_type) is not None)
        if (not isinstance(value_type, PendingListType)
                or not (isinstance(value, (TpyArrayLiteral, TpyListRepeat))
                        or rows_comp)):
            return None
        info = self.ctx.list_literal(value_type.literal_id)
        if (info is None or info.elem_cells is not None
                or info.has_explicit_annotation):
            return None
        return info

    def _analyze_fresh_binding(self, value: TpyExpr) -> TpyType:
        """Analyze the whole value of an unannotated first binding of a
        name, the one binding a diagnostic can suggest annotating."""
        saved = self.ctx.name_initializer
        self.ctx.name_initializer = value
        try:
            return self.expr.analyze_expr(value)
        finally:
            self.ctx.name_initializer = saved

    def _refuse_later_annotation(self, stmt: TpyVarDecl) -> None:
        """An annotation declares a local's one type -- a module global's
        too, the module's statements being one body -- so it stands on the
        first binding (`scan_first_bindings`): past it, the code before was
        already compiled at the type the first binding gave. A parameter is
        bound by its own annotation."""
        func = self.ctx.func
        if stmt.type is None or func.current_function is None:
            return
        if stmt.name in func.current_param_names:
            first = func.current_function
        else:
            sites = func.first_bindings.get(stmt.name)
            if sites is None or any(s is stmt for s in sites):
                return
            first = sites[0]
        line = getattr(getattr(first, "loc", None), "line", None)
        at = f" at line {line}" if line is not None else ""
        raise self.ctx.error(
            f"'{stmt.name}' is already bound{at}; an annotation goes on the "
            f"first binding", stmt)

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
        # in generic scopes and substitutes enum placeholders.
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
                # An init-only function-local `p: Optional[Pet] = Dog()` lowers
                # to a `const Pet*` pointing at the materialized rvalue, same
                # shape as the parameter position. Allow the pointer-repr
                # @dynamic Optional here; the rvalue-rebind reject (shared slot
                # can't retype) and definite-assignment keep it sound. A module
                # slot and a no-init declaration stay rejected -- a static slot
                # has no value representation for an abstract base.
                allow_ptr_repr_dyn = (
                    stmt.init is not None
                    and not self.ctx.is_module_slot_stmt(stmt))
                self.type_ops.validate_type(
                    stmt.type, allow_type_param_ref=self.ctx.in_generic_scope(),
                    loc=stmt.loc,
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
            self._refuse_later_annotation(stmt)
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
                    and self.compat.write_binds_local(stmt.name)):
                # The global's type is not this write's history.
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
        pending_cell = (self.pend.cell_for(stmt.name)
                        if stmt.type is None and not is_global_declared
                        else None)
        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            ctor_td = generic_constructor_factory(self.ctx, stmt.init)
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
                            self.ctx.container_literals[literal_id] = info
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
                existing_elem = (self.pend.list_cells(existing_type)
                                 if stmt.type is None else None)
                if existing_elem is not None:
                    # The list's element so far hints the new value, as a
                    # typed element would; asking it settles nothing.
                    type_hint = self.pend.so_far(existing_type,
                                                      existing_elem)
                if pending_cell is not None:
                    # Its type so far seeds a generic and converts nothing,
                    # and asking it settles nothing.
                    type_hint = self.pend.known_so_far(pending_cell)
                if (type_hint is not None and stmt.type is None
                        and self.deduction.declared_slot_type(
                            stmt.name, existing_type) is None):
                    type_hint = SlotHint.inferred_local(type_hint)
                inner = self.pend.value_binds_first(
                    stmt.name,
                    unbound=(stmt.type is None and not is_global_declared
                             and pending_cell is None
                             and existing_type is None))
                # A store into a local, or into an annotated numeric one, is
                # a consumer a pending number reaches.
                # A first binding of a name some other form binds too
                # (a `nonlocal` store, a loop target...) gets no cell, so
                # it takes a concrete value -- unless its bindings join,
                # which every such form records into the cell.
                takes_pending = (
                    not is_global_declared
                    and ((stmt.type is None
                          and (existing_type is not None
                               or pending_cell is not None
                               or not self.pend.is_excluded(stmt.name)
                               or inner.joins))
                         or is_numeric_slot(stmt.type)))
                # A second name for a list literal leaves its element open.
                alias = (stmt.init if stmt.type is None
                         and not is_global_declared
                         and isinstance(stmt.init, TpyName)
                         and (existing_type is None
                              or existing_elem is not None) else None)
                # So does a row read (`g[0]`, `g.pop()`): the name binds a
                # row, one more list of the rows' element.
                row_read = (stmt.init if stmt.type is None
                            and not is_global_declared
                            and existing_type is None
                            and ((isinstance(stmt.init, TpySubscript)
                                  and not isinstance(stmt.init.index,
                                                     TpySlice))
                                 or isinstance(stmt.init, TpyMethodCall))
                            else None)
                with self.pend.sink(stmt.init if takes_pending else None), \
                        self.pend.list_sink(alias or row_read):
                    if type_hint is None and not is_global_declared:
                        init_type = self._analyze_fresh_binding(stmt.init)
                    else:
                        # An annotated binding and a rebinding of a declared
                        # local coerce the value to that declared type.
                        init_type = self.expr.analyze_at_slot(
                            stmt.init, type_hint, CoercionContext.INIT)
                if stmt.type is None and not is_global_declared:
                    init_type = self._bind_list_elem_cell(
                        stmt.name, stmt, stmt.init, init_type, existing_type)
                    if (bound_inside := inner.cell()) is not None:
                        pending_cell, existing_type = bound_inside

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
                if self.final_is_runtime(stmt.type, stmt.init):
                    self.ctx.runtime_finals.add(stmt.name)

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
                        info = self.ctx.container_literals[init_type.literal_id]
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
                if isinstance(init_type, ReadonlyType):
                    var_type = make_readonly(var_type)
                self.deduction.set_authoritative_annotation(
                    stmt.name,
                    stmt.type,
                    line=(stmt.loc.line if stmt.loc else None),
                )
            elif pending_cell is not None:
                var_type, stmt.init = self._store_pending_num(
                    pending_cell, stmt.init, init_type, stmt)
            elif existing_type:
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
                    site_kind = ("loop" if stmt.name in self.ctx.func.loop_vars
                                 else "with" if stmt.name
                                 in self.ctx.func.with_target_names
                                 else None)
                    if (isinstance(existing_type, OwnType)
                            and site_kind is not None
                            and first_binding_storage(
                                site_kind, existing_type, rebound=True,
                                hoisted=False).pointer):
                        # A rebound owning for or with target is a pointer
                        # over its object (the storage policy): a rebind
                        # re-points it rather than copying into it, so it
                        # checks against the pointee, and the binding keeps
                        # its owning type.
                        _pointee, stmt.init = self.compat.coerce_reassignment(
                            stmt.name, existing_type.wrapped, init_type,
                            stmt.init, stmt)
                        var_type = existing_type
                    else:
                        var_type, stmt.init = self.compat.coerce_reassignment(
                            stmt.name, existing_type, init_type, stmt.init,
                            stmt)
                self.deduction.join_rebind(
                    stmt.name, existing_type, var_type,
                    self.ctx.func.current_scope,
                    stmt.loc.line if stmt.loc else 0)
            else:
                # New variable: resolve IntLiteralType/FloatLiteralType.
                joins_stores = self.pend.joins_bindings(stmt.name, init_type)
                if (not is_global_declared
                        and (((self.pend.is_pending_name(stmt.name)
                               or self.pend.arm_group(stmt.name))
                              and value_family(strip_int(init_type)) is not None)
                             or is_pending_num(init_type) or joins_stores)):
                    # A literal-seeded local, one first bound to a value over
                    # one, one sibling arms bind together, or a rebound one
                    # first bound to a fixed-width integer: its type is
                    # decided when the body is.
                    pending_cell = self.pend.new_cell(
                        stmt.name, stmt,
                        derived=not self.pend.is_pending_name(stmt.name),
                        is_float=bool(value_family(strip_int(init_type))))
                    pending_cell.joins_stores = joins_stores
                    var_type, stmt.init = self._store_pending_num(
                        pending_cell, stmt.init, init_type, stmt)
                    self.ctx.var_types[stmt] = var_type
                    pending_cell.decls.append(stmt)
                elif isinstance(init_type, IntLiteralType):
                    var_type = self.ctx.default_int_for_literal(init_type, warn_node=stmt.init)
                    note_elem_binding(self.ctx, stmt.init)
                elif isinstance(init_type, FloatLiteralType):
                    var_type = FLOAT  # float literals always default to float64
                    note_elem_binding(self.ctx, stmt.init)
                # Preserve OwnType on variables -- Own[T] indicates the variable
                # owns its storage and can be moved at last use.
                # Exceptions: union types need the raw type for isinstance/
                # narrowing codegen; optional pointer types use T* storage.
                elif isinstance(init_type, OwnType):
                    inner = init_type.wrapped
                    if (isinstance(inner, UnionType)
                            or (pointer_repr_optional(inner) is not None)):
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
            elif (isinstance(var_type, OptionalType)
                    and not existing_type
                    and unwrap_optional_own(var_type) is not None
                    and self._names_unowned_storage(stmt.init)):
                # The same for a new local bound to an `Own[P] | None`
                # source naming existing storage it does not own: it binds
                # `P | None` (a readonly payload stays: `readonly[P] |
                # None`) and aliases it. A fresh value is the target's own
                # storage, and a source that is a whole owning binding
                # moves at its last use, so both keep the marker; a rebind
                # stores into the slot its first binding declared.
                var_type = drop_own_marker(var_type)
                # Recorded on the decl: the init expr keeps the marker.
                self.ctx.var_types[stmt] = var_type
                select = peel_coerce(stmt.init)
                if isinstance(select, TpyIfExpr):
                    # A select of places is no fresh value: its own type
                    # drops the marker too, so it reads as the borrow its
                    # arms are.
                    self.ctx.set_expr_type(select, var_type)
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
                    if not self.compat.claim_name_move(
                            stmt.init, var_type, f"'{stmt.name}'"):
                        raise self.ctx.error(
                            f"binding '{stmt.name}' moves the coroutine out of "
                            f"'{stmt.init.name}' (coroutine handles are "
                            f"single-use); '{stmt.init.name}' is used again "
                            f"later",
                            stmt)
                    self.ctx.func.consumed_vars.add(stmt.init.name)
            # Preserve Ref on non-reassigned function locals from reference
            # sources (call returns, field access, subscript, params).
            # Strip for: reassigned locals (T* codegen), top-level globals,
            # and a declared call's fresh value, which the local owns.
            if (stmt.name in self.ctx.func.current_reassigned_vars
                    or self.ctx.is_top_level
                    or binds_fresh_call_value(stmt.init)):
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

        # Record scope depth for new variables (not reassignments of outer-scope
        # vars), ahead of the view rule, which reads it at the bind. Uses scope
        # lookup rather than var_scope_depth existence, so that stale entries
        # from discarded inner scopes get overwritten correctly.
        #
        # Note: when an outer-scoped variable is reassigned with an rvalue inside
        # an inner scope (e.g. `p = Point()` in a loop body where `p` was declared
        # outside), the depth stays at the outer scope. This is safe because the
        # codegen uses a rebind slot at the declaration scope for rvalue rebinds.
        if existing_type is None:
            self.ctx.declare_local(stmt.name, self.ctx.func.current_scope.depth)
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
            # An INFERRED module-slot binding is a global exactly as an
            # annotated one is; `global_scope` is the table the export
            # collection and the storage-durability checks read, so a binding
            # left only in the module-init scope is invisible to both.
            bound = self.ctx.define_module_global(
                stmt.name, var_type, stmt.loc.line if stmt.loc else 0)
            if bound is not var_type:
                var_type = bound
                self.ctx.var_types[stmt] = var_type
                self.ctx.func.current_scope.define(stmt.name, var_type)
        # Reassignment revives a consumed variable
        self.ctx.func.consumed_vars.discard(stmt.name)
        # Flag the local if it's bound to a tuple literal with a FRESH
        # non-value member (would dangle at a later bare-name yield/return);
        # clears on any other reassignment.
        self.compat.update_tuple_member_local_facts(stmt.name, var_type, stmt.init)
        update_ephemeral_alias_fact(self.ctx, stmt.name, var_type, stmt.init)
        # Reassigning a loop variable prevents const-ref binding
        if existing_type is not None:
            mark_roots(self.ctx, [StorageKey.of_name(stmt.name)],
                       MarkRequest(params=False, loop_vars=True))
        if stmt.init is not None and var_type is not None:
            self._mark_bound_loop_var_sources(stmt.name, var_type, stmt.init)
        # Borrow tracking: reassignment breaks aliases in both directions.
        # `retarget_storage_borrows` runs before `remove_borrower` so that
        # borrowers of `name` get re-pointed to `name`'s former upstream
        # (with kind promoted to the most-restrictive in the chain) rather
        # than silently dropped. Required for chains through reassigned vars
        # (e.g. `view = s; s = items[1]`) to keep tracking the source.
        _handle_pinned_view_rebind(self.ctx, stmt.name, stmt)
        bt = self.ctx.func.borrow_tracker
        stamp_bind_kind(self.ctx, stmt, stmt.name, stmt.init, var_type,
                        rebind=existing_type is not None)
        bt.rebind_borrower(stmt.name, stmt.init)
        if (stmt.init is not None and var_type is not None
                and self.compat.settles_body_local(stmt.name, module=True)):
            # The binding is an owning sink of what it owns (`SinkRead.binds`).
            self.compat.register_sink(stmt.init, unwrap_ref_type(var_type),
                                      TupleSink.LOCAL, per_leaf=True,
                                      binds=stmt)
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
            if var_type.is_value_type() and isinstance(var_type, PtrType):
                # take_ptr(x) / Ptr(x) borrows x's storage even though Ptr is a value type
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                ctor_arg = borrowing_ctor_arg(init_inner)
                if ctor_arg is not None:
                    root = borrow_key(ctor_arg)
                    if root is not None:
                        bt.add_borrow(root, stmt.name, BorrowKind.PTR)
                # Direct value-to-Ptr coercion (`pp: Ptr[T] = x`): same borrow
                # semantics as the take_ptr / Ptr(x) explicit forms. The mutation
                # signal is already propagated in coerce_expr; this block adds
                # the borrow registration so warn_borrow_* diagnostics fire.
                if (isinstance(stmt.init, TpyCoerce)
                        and stmt.init.coercion.produces_non_null_ptr):
                    root = borrow_key(stmt.init.expr)
                    if root is not None:
                        bt.add_borrow(root, stmt.name, BorrowKind.PTR)
            elif is_span(var_type):
                # Span from slicing borrows the source container.
                # (StrView/BytesView slices are handled in their own branch below.)
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                if isinstance(init_inner, TpySubscript) and isinstance(init_inner.index, TpySlice):
                    root = borrow_key(init_inner)
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
                        and view_source_is_temporary(self.ctx, init_inner)):
                    raise self.ctx.error(
                        temporary_view_bind_message(
                            var_type, f"'{stmt.name}'", stmt.name),
                        stmt,
                    )
                # A view from slicing a stable source borrows it: register the
                # element borrow so a later in-place mutation of the source
                # warns -- `s += ...` reallocates str's std::string buffer (and
                # a bytearray resizes) just like a container, invalidating the
                # view. Mirrors the Span-slice branch above.
                if isinstance(init_inner, TpySubscript) and isinstance(init_inner.index, TpySlice):
                    root = borrow_key(init_inner)
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
        # An INFERRED str/bytes local registers no loan at all: every write
        # to what it views (a rebind, `+=`, a mutating method, `del`) is a
        # source-storage event the deduction answers by owning the local,
        # so a loan here could only warn about a borrow that no longer
        # exists. A spelled `StrView` / `BytesView` cannot fall back, and
        # keeps the loan and its mutate-while-borrowed diagnostic.
        if (stmt.init is not None
                and stmt.name in self.ctx.func.current_reassigned_vars
                and var_type is not None):
            self._mark_rebound_roots(var_type, stmt.init, rebind=False)
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
        # Update rvalue status for hoist eligibility (both new vars and reassignments)
        if stmt.init:
            record_name_binding(self, stmt.name, var_type, stmt.init,
                                fresh=existing_type is None,
                                init_type=init_type)

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
            if pending_cell is not None:
                pending_cell.decl_keys.append((stmt.loc.line, stmt.name))
        if (pending_cell is None and var_type is not None
                and value_leaves(var_type)):
            # A local bound to a tuple read from a list whose leaves cells
            # decide holds those leaves: its recorded types follow them.
            self._follow_decl(stmt, lambda t, rewrite: self.pend.defer(
                stmt, (t,), lambda _types: rewrite(self.pend.finalize_values)))
        row = (self.pend.container_cells(var_type) if var_type is not None
               else None)
        if (pending_cell is None and row is not None
                and row.info.expr is not stmt.init):
            # A container read off one whose leaves cells decide (`row =
            # g[0]`, `inner = dd["a"]`): its record rewrites only the
            # declaration that binds its literal, so this one follows the
            # resolved types itself.
            self._follow_decl(stmt, lambda t, rewrite: (
                self.ctx.func.after_list_resolution.append(
                    (t, lambda resolved: rewrite(lambda _old: resolved)))))

    def _follow_decl(self, stmt: TpyVarDecl,
                     follow: Callable[[TpyType, _Rewrite], None]) -> None:
        """The types recorded for declaration `stmt` -- its `var_types`
        entry and the declared type the annotations check -- hold what is
        decided later: `follow(t, rewrite)` is given each recorded type and
        arranges for `rewrite(f)` to replace it by `f` of its value then."""
        key = (stmt.loc.line, stmt.name) if stmt.loc else None
        for table, k in ((self.ctx.var_types, stmt),
                         (self.ctx.declared_var_types, key)):
            if k is None or k not in table:
                continue

            def rewrite(f: Callable[[TpyType], TpyType], table: dict = table,
                        k: object = k) -> None:
                table[k] = f(table[k])
            follow(table[k], rewrite)

    def _unpack_store_type(self, name: str, existing: TpyType,
                           elem_type: TpyType, elem_expr: TpyExpr | None,
                           stmt: TpyTupleUnpack, index: int) -> TpyType:
        """The type an unpack target already bound stores element `index`
        at. An element that would narrow into the slot is refused: the
        unpack assigns each element as it is, with no place for the range
        check the narrowing needs (BUGS.md#unpack-target-narrowing)."""
        self.deduction.refuse_int_float_rebind(
            name, existing, elem_type, elem_expr, site=stmt)
        target = existing
        coercion = self.compat.check_type_compatible(
            elem_type, target, "tuple unpacking", source_expr=stmt)
        if coercion is not None and coercion.narrows_checked:
            raise self.ctx.error(
                f"Type mismatch in tuple unpacking: element {index} is "
                f"{python_type_name(elem_type)}, which '{name}' "
                f"({python_type_name(int_payload(target))}) does not hold "
                f"every value of; unpack it into a new name and assign "
                f"that to '{name}'", stmt)
        return target

    def _analyze_tuple_unpack(self, stmt: TpyTupleUnpack) -> None:
        """Analyze tuple unpacking: a, b = expr. A tuple read from a list
        whose leaves cells decide reaches it undecided: each target binds
        its leaf, as `a = xs[1][0]` does."""
        with self.pend.list_sink(stmt.value):
            rhs_type = self.expr.analyze_expr(stmt.value)
        rhs_check = rhs_type.wrapped if isinstance(rhs_type, OwnType) else rhs_type
        # A readonly tuple (a `readonly[list[tuple[..]]]` element, a readonly
        # param) unpacks like the bare one; its reference elements stay
        # readonly in their targets.
        rhs_readonly = isinstance(rhs_check, ReadonlyType)
        rhs_check = unwrap_readonly(rhs_check)

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
        has_owned_elem = any(unwrap_optional_own(et) is not None
                             for et in rhs_type.element_types)
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
        # An element a binding of the source local borrows is no object of
        # its own to hand over: its target binds it by reference, as it
        # would were the source still read.
        borrowed_elems = (self.ctx.func.nonowning_elems(stmt.value.name,
                                                        len(rhs_type.element_types))
                          if isinstance(stmt.value, TpyName)
                          and not self.ctx.is_top_level else frozenset())
        if stmt.is_loop_head or not isinstance(stmt.value, TpyName):
            source_consumed = True
        elif self.ctx.is_top_level:
            source_consumed = False
        else:
            # Unpacking an owned-element tuple by NAME consumes the whole
            # source: the targets take the elements it owns.
            takes = any(unwrap_optional_own(et) is not None
                        and i not in borrowed_elems
                        for i, et in enumerate(rhs_type.element_types))
            if takes:
                # The targets own the elements the source hands over where
                # the read consumes it: the read's record says which move.
                self.compat.register_sink(stmt.value, rhs_type,
                                          TupleSink.UNPACK, per_leaf=True)
                source_consumed = self.compat.claim_name_move(stmt.value)
                if self.compat.holds_reference_leaf(rhs_type):
                    self.ctx.assert_slot_effect("an unpack's ownership claim")
                    self.ctx.func.owning_reads.append(
                        UnpackClaim(stmt.value.name, stmt.value, stmt))
            else:
                source_consumed = False
                self.compat.retract_hidden_borrowed_last_use(stmt.value)
        # Per-element expressions for narrowing (range facts, etc.)
        has_elem_exprs = isinstance(stmt.value, TpyTupleLiteral)
        records = unpack_target_records(
            self.ctx, stmt.value, rhs_type, readonly=rhs_readonly,
            consumed=source_consumed, borrowed=borrowed_elems,
            step=stmt.step_handover if stmt.is_loop_head else None)
        for i, name in enumerate(stmt.targets):
            elem_expr = stmt.value.elements[i] if has_elem_exprs and i < len(stmt.value.elements) else None
            rec = records[i]
            owned, is_ref, elem_type = rec.owned, rec.ref, rec.type
            stmt.is_owned.append(owned)
            stmt.is_ref.append(is_ref)
            if (is_ref and name is not None
                    and not isinstance(stmt.value, TpyTupleLiteral)):
                # The target refers to the source's element (a literal's is
                # copied in, see below).
                note_nonowning_bind(self.ctx, name, stmt.value,
                                    source_path=(i,))
            stmt.is_rebound.append(
                is_ref and name is not None
                and name in (stmt.loop_body_rebinds if stmt.is_loop_head
                             else self.ctx.func.current_reassigned_vars))

            stmt.target_types.append(elem_type)

            if name is None:
                stmt.is_new.append(True)
                continue

            # Block reassignment of Final globals at module level
            if self.ctx.is_top_level and name in self.ctx.final_globals:
                raise self.ctx.error(
                    f"Cannot reassign Final variable '{name}'", stmt)
            existing = self.ctx.func.current_scope.lookup(name)
            if existing is not None and self.compat.write_binds_local(name):
                existing = None
            if existing is not None:
                self._check_nonvalue_rebinding(name, stmt)
                if self.pend.rebind(name, elem_type, elem_expr, stmt) is None:
                    self._unpack_store_type(name, existing, elem_type,
                                            elem_expr, stmt, i)
                self.narrowing.update_after_write(name, existing, elem_type, elem_expr)
                # Mirror the scalar var-decl, which marks assigned on every
                # init path (statements.py `if stmt.init: mark_assigned`): a
                # branch that reassigns a target the sibling branch declared
                # must re-mark it so merge_branches keeps it definitely
                # assigned (drives if/else predecl pre-declaration).
                self.init.mark_assigned(name)
                stmt.is_new.append(False)
            else:
                # A depth-0 statement's target is a module global: the one
                # difference from a local is its namespace-scope storage.
                slot = self.ctx.is_module_slot_stmt(stmt)
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
                # A leaf a cell has not decided yet binds as `a = xs[1][0]`
                # does, and a local joining its bindings as `a = p[0]`: the
                # target is declared at its own derived cell.
                read_type, elem_type = self.pend.derived_binding(
                    name, elem_type, stmt, loop_head=stmt.is_loop_head)
                stmt.target_types[i] = elem_type
                if slot:
                    self.ctx.define_module_global(
                        name, elem_type, stmt.loc.line if stmt.loc else 0)
                self.ctx.func.current_scope.define(name, read_type)
                if stmt.is_loop_head:
                    register_binder(self.ctx, self.ctx.func.current_scope,
                                    name, stmt)
                self.ctx.func.nonstmt_bound_names.add(name)
                # Record scope depth so the definite-assignment read-check sees
                # the target (mirrors the scalar var-decl): a loop-body-only
                # target used after the loop is rejected, not miscompiled.
                self.ctx.declare_local(name, self.ctx.func.current_scope.depth)
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
                if slot:
                    # Declared at namespace scope (`gen_global_decl`), so
                    # the unpack assigns it.
                    self._warn_all_caps_without_final(name, elem_type, stmt)
                    if stmt.loc:
                        self.ctx.declared_var_types[(stmt.loc.line, name)] = (
                            unwrap_own(elem_type))
                stmt.is_new.append(not slot)
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
                if stmt.is_loop_head:
                    # A view of the current step's element, which the next
                    # step replaces: it lends no view to a local.
                    self.deduction.mark_view_non_lending(fam, tgt_id)
                elif not (isinstance(stmt.value, TpyName)
                          and self.deduction.name_holds_storage(
                              stmt.value.name)):
                    # An element of an rvalue (or a field / container
                    # element, or a NAME that only borrows one) source: the
                    # target owns it, moved out of a disposable holder by
                    # the lowering.
                    self.deduction.mark_view_reassigned_from_owned(name, fam)
                # A per-statement capture (call / rvalue source) has no name
                # the hoist rule can place; a NAME source is that tuple.
                self.deduction.note_view_binding(
                    fam, tgt_id, stmt.value if source_binds_by_ref else None,
                    replace=True)
            # A view target off a NAME source views that tuple's own element,
            # exactly as `a = t[i]` does, so it registers the same source
            # storage and a reseat of the tuple demotes it to owned.
            if source_binds_by_ref and not stmt.is_loop_head:
                fam = self.deduction.tuple_target_view_family(name)
                src_id = self.ctx.view_var_map(fam).get(name) if fam else None
                if fam is not None and src_id is not None:
                    bt = self.ctx.func.borrow_tracker
                    self.deduction.register_view_source_storage(
                        fam, src_id, bt.canonical_key(
                            bt.effective_storage(stmt.value.name)))
            if stmt.loc:
                display_type = unwrap_own(elem_type) if elem_type else elem_type
                self.ctx.declared_var_types[(stmt.loc.line, name)] = display_type

        stamp_unpack_rebinds(self.ctx, stmt)
        if any(t is not None and contains_pending_leaf(t)
               for t in stmt.target_types):
            self.ctx.func.pending_elem_type_fields.append(
                (stmt, "target_types"))

        if value_leaves(rhs_type):
            # The targets bound leaves of a list's element: their recorded
            # types follow the cells. A target with a derived cell of its own
            # is decided with its function.
            decided = self.pend.finalize_settled

            def follow(_types: tuple[TpyType, ...]) -> None:
                stmt.target_types[:] = [decided(t) for t in stmt.target_types]
                for name in stmt.targets:
                    key = (stmt.loc.line, name) if stmt.loc else None
                    if key in self.ctx.declared_var_types:
                        self.ctx.declared_var_types[key] = decided(
                            self.ctx.declared_var_types[key])
            self.pend.defer(stmt, (rhs_type,), follow)

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
                    # Declares no copy (`copy_warning`): the target is a
                    # local binding, no owning sink with a source record.
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
            name_src_root = (holder_root(stmt.value, MayLend(self.ctx),
                                         proven=True)
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
                    src_root = holder_root(literal_src.elements[i],
                                           MayLend(self.ctx), proven=True)
                else:
                    src_root = name_src_root
                if src_root is None or src_root == name:
                    continue
                # A loop var over an iterator whose yield names each element's
                # own source (`zip(xs, ys)`) lends the target that source
                # directly, so a write through it climbs to `xs` alone.
                src_loop = self.ctx.loop_sources_of(src_root)
                elem_srcs = src_loop.elems if src_loop is not None else None
                lender_sites = (list(elem_srcs[i])
                                if elem_srcs is not None and i < len(elem_srcs)
                                else [SitedKey(StorageKey.of_name(src_root),
                                               self.ctx.binding_site(src_root))])
                lenders = [s.key for s in lender_sites]
                through_pointer = (literal_src is None and elem_srcs is None
                                   and isinstance(stmt.value, TpyName)
                                   and self._owned_element_is_pointer(
                                       stmt.value.name, i))
                # Off a tuple name the target points into element `i` of it.
                unpacked = (("lit", i) if literal_src is None
                            and elem_srcs is None
                            and isinstance(stmt.value, TpyName) else None)
                for lender in lenders:
                    bt.add_borrow(lender, name, BorrowKind.ELEMENT,
                                  elem_index=unpacked,
                                  through_pointer=through_pointer)
                # A reassigned target binds `T* x = &(element)`, one type
                # for every referent it is re-pointed at, so the source must
                # stay mutable -- the reassigned scalar alias's rule. The
                # reassign drops the loan, so a write after it cannot climb.
                if stmt.is_rebound[i]:
                    mark_roots(self.ctx, lender_sites,
                               MarkRequest(loop_vars=True))

        # Mirror the scalar Own[T] consume model -- at the last use mark the
        # source consumed (drives the unconsumed-param warning); a move-only
        # (nocopy) source used after this point can't be moved out, so it is
        # the same use-after-move error scalar Own raises rather than the raw
        # deleted-copy C++ error. The source of an Own param's elements hands
        # each owned element to its target, so the warning then asks per
        # element whether its target was consumed.
        for name in stmt.targets:
            if name is not None:
                self.ctx.func.own_consume_aliases.pop(name, None)
        if (not self.ctx.is_top_level and any(stmt.is_owned)
                and isinstance(stmt.value, TpyName)
                and self.compat._is_owned_var(stmt.value.name)):
            # Consumption marks only: the unpack's move is the
            # `claim_name_move` record taken above.
            if self.compat.is_last_use_move_candidate(stmt.value):
                key = own_consume_key(self.ctx, stmt.value.name)
                if key is None or key.index is not None:
                    self.compat.check_own_consumption(stmt.value)
                else:
                    for i, name in enumerate(stmt.targets):
                        if stmt.is_owned[i] and name is not None:
                            self.ctx.func.own_consume_aliases[name] = OwnSlot(key.param, i)
            elif self.ctx.is_type_non_copyable(rhs_type):
                reason = (self.ctx.nocopy_reason(rhs_type)
                          or f"owned tuple '{stmt.value.name}'")
                raise self.ctx.error(
                    f"{reason} is used after this point and cannot be unpacked "
                    f"by move. Remove later uses or restructure the code.",
                    stmt.value)

        # Determine const-ref eligibility per element for expensive value types.
        # Safe because tuples are immutable -- no in-place mutation possible
        # -- where nothing replaces the tuple under the reference: a name no
        # write reseats (a reseat of it is followed), or the statement's own
        # value. A field or an element of a container is replaced unseen.
        if not self.ctx.is_top_level:
            if isinstance(stmt.value, TpyName):
                source_safe = not self.ctx.is_reseated(stmt.value.name)
            else:
                source_safe = is_rvalue_source(self.ctx, stmt.value)
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

    def _written_key_type(self, target: TpySubscript) -> TpyType:
        """The type of subscript `target`'s key as written: the slice a
        written slice builds, else what its analysis gave."""
        if isinstance(target.index, TpySlice):
            return SLICE if target.is_stepped_slice else BASIC_SLICE
        key = target.written_index if target.written_index is not None \
            else target.index
        key_type = self.ctx.get_raw_expr_type(key)
        assert key_type is not None
        return key_type

    def _credit_subscript_setitem(self, target: TpySubscript, stmt: TpyStmt,
                                  *, check_loans: bool) -> None:
        """A `__setitem__` without the element-access contract (a record's,
        a builtin slice store's) is a method call on the receiver that may
        grow it, unlike an element store (answered by the element-hop check
        at the write site): credit it through the receiver-call
        chokepoint, so the structural mark
        reaches a caller holding a loan across the enclosing call.
        `check_loans=False` when the element-hop check already warned at
        this write."""
        callee = target.dunder(SubscriptDunder.SET)
        if callee is None or callee.root.element_access is not None:
            return
        recv = self._subscript_receiver(target)
        assert recv is not None
        # A builtin's dunder is no name the user wrote: the warning names
        # the store.
        label = self.expr.subscript_arg_context(target, recv[0], 1, [callee])
        credit_implicit_receiver_call(self.ctx, target.obj, recv[0], callee.root,
                                      callee.name, stmt,
                                      check_loans=check_loans, label=label)

    def _subscript_receiver(
            self, target: TpySubscript) -> 'tuple[TpyType, bool] | None':
        """The receiver a subscript calls its dunders on -- a checked
        Optional answering as its inner type -- and whether it is
        readonly; None for a subscript that is no dunder call (a tuple
        element, a TypedDict field, a protocol receiver)."""
        recv = self.ctx.get_raw_expr_type(target.obj)
        if recv is None or target.typed_dict_field is not None:
            return None
        recv = unwrap_own(unwrap_ref_type(recv))
        readonly = isinstance(recv, ReadonlyType)
        bare = unwrap_qualifiers(recv)
        if isinstance(bare, OptionalType):
            bare = unwrap_qualifiers(bare.inner)
        if isinstance(bare, TupleType) or is_protocol_type(bare):
            return None
        return bare, readonly

    def _resolve_subscript_store(self, target: TpySubscript,
                                 key_type: TpyType,
                                 value_type: TpyType,
                                 value: TpyExpr) -> TpyType:
        """`t[k] = v` is the explicit `t.__setitem__(k, v)` call: its
        variant resolved on the key and the value
        (`ExpressionAnalyzer.resolve_subscript_dunder`), the key received
        at it; the value is the store's, coerced to the variant's value
        parameter (`store_slot_type`), which this returns as the target's
        type."""
        recv = self._subscript_receiver(target)
        assert recv is not None
        receiver, readonly = recv
        fi = self.expr.resolve_subscript_dunder(
            target, self.expr.subscript_views(receiver, SubscriptDunder.SET),
            readonly, [key_type, value_type], stored=value)
        slot = make_ref(store_slot_type(fi))
        self.ctx.set_expr_type(target, slot)
        return slot

    def _literal_subscript_store(
            self, stmt: TpyAssign, receiver: TpyType, key_type: TpyType,
            value_hint: 'TpyType | None',
    ) -> tuple[TpyType, TpyType, bool]:
        """`c[k] = v` into a container literal: the call of its
        `__setitem__` takes the literal's preparation
        (`MethodAnalyzer.prepare_container_call`), which analyzes the value
        and stores it -- the key too where the key is one of the
        container's parts (a dict's) -- then resolves as any store does.
        Returns the store's target type (the part the cells that took the
        value have, else the variant's value parameter), the value's type
        as the container takes it, and whether cells took it."""
        sub = stmt.target
        assert isinstance(sub, TpySubscript)
        args = [sub.index, stmt.value]
        prepared = self.expr.methods.prepare_container_call(
            stmt, sub.obj, receiver, SubscriptDunder.SET.value, args,
            evidence={0: key_type},
            what=self.expr.describe_pending_use(sub.obj))
        sub.index, stmt.value = args
        pre = prepared.args or [None, None]
        key_type = pre[0] if pre[0] is not None else key_type
        call_recv = unwrap_readonly(unwrap_ref_type(prepared.receiver))
        sv = self.expr.subscript_views(call_recv, SubscriptDunder.SET)
        if pre[1] is None:
            # The preparation left the value alone (the call decided the
            # literal first): it is hinted at the receiver as decided.
            hint = (self.expr.subscript_store_hint(sub, sv, key_type)
                    if call_recv is not receiver else value_hint)
            value_type = self.expr.analyze_at_slot(
                stmt.value, None if isinstance(
                    unwrap_ref_type(hint), UnknownElementType) else hint,
                CoercionContext.ASSIGN)
        else:
            value_type = pre[1]
        fi = self.expr.resolve_subscript_dunder(
            sub, sv, False, [key_type, value_type], stored=stmt.value)
        stored = prepared.stored
        target = (make_ref(at_path(stored.tree, stored.tree.STEPS[-1:]))
                  if stored is not None else make_ref(store_slot_type(fi)))
        self.ctx.set_expr_type(sub, target)
        return target, value_type, stored is not None

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
        # D16 dyn-attr write: if the target is `obj.foo` where foo is undeclared
        # AND the class has __setattr__, the read-side analysis would either
        # resolve via __getattr__ (handled below) or raise "no field". Detect
        # the latter from the receiver, analyzed ahead of the target, which
        # then meets it through its record rather than evaluating it again.
        recv_type = (self._write_target_receiver(stmt.target)
                     if isinstance(stmt.target, TpyFieldAccess) else None)
        if (recv_type is not None
                and self._target_is_dyn_writable_only(stmt.target, recv_type)):
            self._analyze_dyn_setattr_assign(stmt)
            return
        if isinstance(stmt.target, TpyFieldAccess):
            # Pin the write position before the target is analysed as a read:
            # a property read otherwise becomes its GETTER call and the setter
            # below would have no field name to look up.
            stmt.target.is_write_target = True
        elif isinstance(stmt.target, TpySubscript):
            stmt.target.is_write_target = True
        pending_cell = (self.pend.cell_for(stmt.target.name)
                        if isinstance(stmt.target, TpyName) else None)
        # The target of a store is no use of a pending local, of a list
        # literal's element, or of the list a name is rebound from.
        with self.pend.sink(stmt.target if pending_cell is not None
                            or isinstance(stmt.target, TpySubscript)
                            else None), \
                self.pend.list_sink(stmt.target
                                    if isinstance(stmt.target,
                                                  (TpyName, TpySubscript))
                                    else None):
            target_type = self.expr.analyze_expr(stmt.target)
        # A subscript store is its `__setitem__` call; one into a container
        # literal takes the literal's preparation, which analyzes the value.
        sub_recv = (self._subscript_receiver(stmt.target)
                    if isinstance(stmt.target, TpySubscript) else None)
        literal_store = (sub_recv is not None
                       and self.expr.methods.prepares(sub_recv[0]))
        key_type = (self._written_key_type(stmt.target)
                    if sub_recv is not None else None)
        name_list = (self.pend.cell_container(target_type)
                     if isinstance(stmt.target, TpyName) else None)
        list_cells = (self.pend.container_cells(target_type)
                      if isinstance(stmt.target, TpyName) else None)
        self._check_constant_write(stmt.target, stmt)
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
            mark_roots(self.ctx, storage_roots(
                stmt.target.obj, Use.POINTEE, self.ctx.get_expr_type,
                MayLend(self.ctx)),
                MarkRequest(loop_vars=True, through_field=True))
            self._enforce_readonly_assignment_target(stmt.target)
            return
        # A store into a local, or into a numeric field, is a consumer a
        # pending number reaches; a subscript store's value is its dunder's
        # argument, open where the explicit call's would be.
        setter = (self.expr.subscript_views(sub_recv[0], SubscriptDunder.SET)
                  if sub_recv is not None and not literal_store else None)
        numeric_store = (isinstance(stmt.target, TpyName)
                         or (isinstance(stmt.target, TpyFieldAccess)
                             and is_numeric_slot(target_type))
                         or (setter is not None
                             and self.expr.calls.number_arg_stays_open(
                                 setter.views, setter.shape, 1)))
        store_cell = pending_cell
        if store_cell is not None:
            value_hint = SlotHint.inferred_local(
                self.pend.known_so_far(store_cell))
        elif list_cells is not None:
            value_hint = SlotHint.inferred_local(
                self.pend.so_far(target_type, list_cells))
        elif isinstance(unwrap_ref_type(target_type), UnknownElementType):
            # A store whose `__setitem__` variants taking the key disagree
            # on the value: they are ranked on the value as analyzed, as the
            # call ranks its argument.
            value_hint = None
        else:
            value_hint = target_type
        cell_target = False
        if literal_store:
            assert sub_recv is not None and key_type is not None
            target_type, value_type, cell_target = (
                self._literal_subscript_store(stmt, sub_recv[0], key_type,
                                              value_hint))
        else:
            # A list stored as a row, or rebinding a cell list, is linked
            # to it undecided.
            with self.pend.sink(stmt.value if numeric_store else None), \
                    self.pend.list_sink(stmt.value
                                        if list_cells is not None
                                        and isinstance(stmt.value, TpyName)
                                        else None):
                value_type = (
                    self.expr.analyze_expr_with_hint(stmt.value, value_hint)
                    if store_cell is not None or list_cells is not None
                    else self.expr.analyze_at_slot(stmt.value, value_hint,
                                                   CoercionContext.ASSIGN))
            if sub_recv is not None:
                assert key_type is not None
                # A value its context types ranks at the slot it was hinted
                # with, as an argument hinted by its call's survivors does.
                ranked_value = (self.expr.calls.ranked_at_slot(
                                    value_type, value_hint)
                                if value_hint is not None
                                and self.expr.calls.context_typed(stmt.value)
                                else value_type)
                target_type = self._resolve_subscript_store(
                    stmt.target, key_type, ranked_value, stmt.value)
        if name_list is not None:
            value_type = self._bind_list_elem_cell(
                stmt.target.name, stmt, stmt.value, value_type, target_type)
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
        # Through-reference writes (field/subscript) mutate the param's object;
        # plain name reassignment just rebinds the local.
        mark_roots(self.ctx, storage_roots(stmt.target, Use.PLACE,
                                           self.ctx.get_expr_type,
                                           MayLend(self.ctx)),
                   MarkRequest(loop_vars=True, through_field=True,
                               params=isinstance(stmt.target, (TpyFieldAccess,
                                                               TpySubscript))))
        copy_warning_fired = False
        name_moved = False
        takes_elements = False
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            declared_target_type = self.narrowing.declared_type_for_expr(stmt.target)
            # The element of a list literal stays its cell's: the store is
            # one more value the list holds, and whether a literal fits is
            # known when the element is, as for `append`.
            if declared_target_type is not None and not cell_target:
                target_type = declared_target_type
            setter = (stmt.target.dunder(SubscriptDunder.SET)
                      if isinstance(stmt.target, TpySubscript) else None)
            takes_elements = (setter is not None
                              and store_takes_elements(setter))
            # A view FIELD keeps pointing into its source after the statement:
            # the local rule, at every position the field is written from.
            if (isinstance(stmt.target, TpyFieldAccess)
                    and target_type is not None
                    and view_slot_source_is_temporary(
                        self.ctx, unwrap_qualifiers(target_type),
                        stmt.value)):
                raise self.ctx.error(
                    temporary_view_bind_message(
                        unwrap_qualifiers(target_type),
                        f"field '{stmt.target.field}'", stmt.target.field),
                    stmt,
                )
            # A closure capturing `self` holds this object's address, which a
            # copy or move of the object leaves pointing at the original.
            lam = stmt.value.expr if isinstance(stmt.value, TpyCoerce) else stmt.value
            if (isinstance(stmt.target, TpyFieldAccess)
                    and isinstance(lam, TpyLambda)
                    and "self" in lam.captured_names):
                raise self.ctx.error(
                    f"A lambda stored in field '{stmt.target.field}' cannot "
                    f"capture 'self': a copy or move of the object would leave "
                    f"the closure pointing at the original",
                    stmt,
                )
            # Unified copy detection: Ref (borrowed), Own (owned at non-last-use),
            # or compound borrowed types (Optional/Union with pointer repr) on
            # the value expression type means storing it into a field/container
            # will copy.  Ref covers params, call returns, field access, subscript.
            # Own covers owned locals at non-last-use.
            # Optional[NonValue] and Union[pointer-repr] use pointer representation
            # (T*, variant<A*,B*>) which is semantically borrowed -- copying into
            # storage (std::optional<T>, variant<A,B>) is a pointer-to-value copy.
            # Skip explicit copy() calls (caller acknowledged the copy) and
            # an OwnType rvalue (an explicit Own return from a function).
            is_own_lvalue = (isinstance(value_type, OwnType)
                             and self.compat.is_lvalue(stmt.value))
            stripped_value = self.ctx.get_expr_type(stmt.value)
            is_compound_ref = (
                (isinstance(stripped_value, OptionalType) and not stripped_value.inner.is_value_type())
                or pointer_variant_union(stripped_value) is not None
            )
            # Ptr[T] target takes the address of the source (`_a(&a)`), no copy.
            target_is_ptr = isinstance(unwrap_qualifiers(target_type), PtrType)
            # Any target: INTO_ANY in compatibility.py emits its own copy
            # warning for reference-type sources -- skip the generic
            # field/container warning here to avoid a double-fire.
            target_is_any = (isinstance(unwrap_qualifiers(target_type), AnyType)
                             or takes_elements)
            # Beyond the declared-type facts above, the shared owning-sink
            # source rule: a select or walrus naming an existing object.
            store_dest = ("field" if isinstance(stmt.target, TpyFieldAccess)
                          else "container")
            stores_reference = (
                not unwrap_qualifiers(target_type).is_value_type()
                and stripped_value is not None
                and not unwrap_qualifiers(stripped_value).is_value_type())
            if (stmt.loc is not None and not target_is_any
                    and target_type is not None):
                # A store into `Any` registers with the `Any` coercion.
                self.compat.register_sink(
                    stmt.value, target_type,
                    TupleSink.FIELD if store_dest == "field"
                    else TupleSink.CONTAINER)
            # Whether the store moves the source name in at its last use,
            # asked once for both copy rules below; the copy a later
            # binding settles is the store's, in its words.
            name_moved = (stores_reference
                          and isinstance(stmt.value, TpyName)
                          and self.compat.claim_name_move(
                              stmt.value, OwnType(target_type),
                              sink=SinkCopy(unwrap_qualifiers(value_type),
                                            target_type, store_dest)))
            source_copies = (
                stores_reference and not name_moved
                and self.compat.source_copies_into_storage(stmt.value, False))
            if ((isinstance(value_type, RefType) or is_own_lvalue
                 or is_compound_ref or source_copies)
                    and stmt.loc is not None
                    and not target_is_ptr
                    and not target_is_any
                    and not self.compat.is_copy_call(stmt.value)):
                self.compat.register_sink_copy(
                    SinkCopy(unwrap_qualifiers(value_type), target_type,
                             store_dest), stmt.value)
                copy_warning_fired = True
            # A tuple is a value type, but storing one whose elements are
            # pointer-repr COPIES each element its source lends into the
            # owned slot, built element by element, where CPython aliases --
            # warned per element by the one per-element check.
            if stmt.loc is not None and not target_is_any:
                tgt_tuple = unwrap_qualifiers(target_type)
                dest = "field" if isinstance(stmt.target, TpyFieldAccess) else "container"
                if isinstance(tgt_tuple, TupleType):
                    if self.compat.check_owned_tuple_sink(
                            stmt.value, OwnType(tgt_tuple), dest, stmt):
                        copy_warning_fired = True
                    if isinstance(stmt.value, TpyName):
                        # The store moves the elements the local owns (its
                        # warning above names every reference element).
                        self.compat.claim_name_move(
                            stmt.value, OwnType(tgt_tuple))
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
            elif isinstance(stmt.value, TpyName):
                # A local holding what an Own param moved in, stored.
                self.ctx.mark_own_param_consumed(stmt.value.name)
        if isinstance(stmt.target, TpyName):
            self.ctx.func.own_consume_aliases.pop(stmt.target.name, None)
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
            if pending_cell is not None:
                target_type, stmt.value = self._store_pending_num(
                    pending_cell, stmt.value, value_type, stmt)
            # A container literal reassigned: one local, one element type
            # (its cells, when they decide its leaves); the target stays
            # pending.
            elif isinstance(inner_target, PendingContainerType):
                lc = self.pend.container_cells(inner_target)
                if lc is not None:
                    self.deduction.refuse_int_float_rebind(
                        stmt.target.name, self.pend.so_far(inner_target, lc),
                        inner_value, stmt.value, site=stmt)
                self.deduction.rebind_elem_cell(inner_target, inner_value,
                                                stmt)
                # Lists of different sizes are one list, not an Array.
                if (isinstance(inner_target, PendingListType)
                        and isinstance(inner_value, PendingListType)):
                    if inner_target.size != inner_value.size:
                        self.deduction.mark_list_different_size(inner_target.literal_id)
                        self.deduction.mark_list_different_size(inner_value.literal_id)
                    else:
                        self.deduction.link_list_literals(inner_target.literal_id, inner_value.literal_id)
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
                if isinstance(value_type, ReadonlyType):
                    target_type = make_readonly(target_type)
            self.ctx.func.current_scope.define(stmt.target.name, target_type)
            # Reassignment revives a consumed variable
            self.ctx.func.consumed_vars.discard(stmt.target.name)
            self.compat.update_tuple_member_local_facts(
                stmt.target.name, target_type, stmt.value)
            update_ephemeral_alias_fact(
                self.ctx, stmt.target.name, target_type, stmt.value)
            # Borrow tracking: reassignment breaks aliases in both directions.
            # Retarget runs before remove_borrower so borrowers of the target
            # get re-pointed to the upstream source (with promoted kind),
            # keeping chains through reassigned vars valid.
            _handle_pinned_view_rebind(self.ctx, stmt.target.name, stmt)
            bt = self.ctx.func.borrow_tracker
            # Stamped below, once a tuple literal's element captures (what
            # the binding owns) are annotated.
            bind_value, bind_type = stmt.value, target_type
            bt.rebind_borrower(stmt.target.name, stmt.value)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.update_variable_type(stmt.target.name, target_type)
            self.ctx.set_expr_type(stmt.target, target_type)
            self.deduction.record_write(stmt.target.name, stmt.value, inner_value)
            if (pending_cell is None and not isinstance(
                    inner_target, (*PENDING_CONTAINER_TYPES, PendingViewType))):
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

        # Tuples are immutable -- reject element assignment
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            if isinstance(obj_type, TupleType):
                raise self.ctx.error("Tuples are immutable; cannot assign to tuple elements", stmt)
            # A span over readonly elements is a const view, which the
            # `Span[T].__setitem__` stub cannot exclude for a readonly `T`.
            if is_span(obj_type) and is_readonly_span(obj_type):
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
        # (declared by `mutates="elements"` on dict.__setitem__).
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
            self._credit_subscript_setitem(stmt.target, stmt,
                                           check_loans=hit is None)
        elif isinstance(stmt.target, TpyFieldAccess):
            storage = self._resolve_obj_storage(stmt.target.obj)
            # Also check the field-path key itself (e.g. "self.items" for self.items = [...])
            # since borrows may be registered on the dotted key.
            field_storage = storage_key(stmt.target)
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
                self.ctx.warning(storage_loan_mutation_warning(
                    bt, storage, "field assignment"), stmt)
            self._mark_field_write_views(stmt.target, storage, field_storage)

        # Field / container element targets store the value (storage form);
        # the `T -> Optional[T]` address-take that fires for borrow-form
        # destinations doesn't apply here.
        target_is_storage = isinstance(stmt.target, (TpyFieldAccess, TpySubscript))
        if sub_recv is not None and not cell_target:
            # The value is the `__setitem__` call's argument, received at its
            # parameter as the call receives one: a container literal resolves
            # against it, an empty one at a protocol takes its element.
            slot = unwrap_ref_type(target_type)
            self.expr.calls._maybe_coerce_empty_list_to_protocol(value_type,
                                                                 slot)
            self.expr.calls.mark_pending_slot_context(stmt.value, value_type,
                                                      slot)
        stmt.value = self.compat.coerce_expr(
            stmt.value, value_type, target_type, "assignment",
            coercion_ctx=CoercionContext.ASSIGN,
            target_is_storage_form=target_is_storage,
            inferred_slot=inferred_field_slot(self.ctx, stmt.target))
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.property_setter_call is not None):
            # The setter receives the converted value, as any argument does.
            stmt.target.property_setter_call.args[0] = stmt.value
        # Annotate tuple literal element capture modes. A cell container's
        # store coerced the literal to its part, which reads as a reference;
        # its members land in the container as a declared one's do.
        literal = peel_value_wrappers(stmt.value)
        if isinstance(literal, TpyTupleLiteral):
            tuple_target = _stored_tuple_target(target_type)
            if tuple_target is not None:
                is_field = isinstance(stmt.target, TpyFieldAccess)
                is_subscript = isinstance(stmt.target, TpySubscript)
                self._annotate_tuple_elem_capture(
                    literal, tuple_target,
                    sink_dest=("field" if is_field
                               else "container" if is_subscript
                               else "owned storage"),
                    sink=(TupleSink.FIELD if is_field
                          else TupleSink.CONTAINER if is_subscript
                          else TupleSink.LOCAL),
                    members_checked=isinstance(peel_coerce(stmt.value),
                                               TpyNamedExpr))
        # Residual copy warning: reassigned vars without OwnType in scope
        if isinstance(stmt.target, TpyName):
            # After the literal's element captures: they say which elements
            # the binding keeps a reference into.
            self._mark_rebound_roots(inner_target, stmt.value, rebind=True)
            stamp_bind_kind(self.ctx, stmt, stmt.target.name, bind_value,
                            bind_type, rebind=True)
            if (target_type is not None
                    and self.compat.settles_body_local(
                        stmt.target.name, module=True)):
                # The binding is an owning sink of what it owns
                # (`SinkRead.binds`).
                self.compat.register_sink(
                    stmt.value, unwrap_ref_type(target_type),
                    TupleSink.LOCAL, per_leaf=True,
                    binds=stmt)
            # The declaration rebind's binding record (`x = v`), the one
            # every binding of a name takes.
            record_name_binding(self, stmt.target.name, target_type,
                                stmt.value, fresh=False,
                                init_type=value_type)
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            if (stmt.loc is not None and not copy_warning_fired
                    and not takes_elements):
                if self._is_non_owned_var_copy(stmt, value_type, target_type,
                                               name_moved):
                    self.compat.register_sink_copy(
                        SinkCopy(value_type, target_type,
                                 "field" if isinstance(stmt.target,
                                                       TpyFieldAccess)
                                 else "container"), stmt.value)

        # Scope escape check for assignments to named variables
        if isinstance(stmt.target, TpyName) and not target_type.is_value_type():
            self.scopes.check_escape(stmt.target.name, stmt.value, stmt)
        if isinstance(stmt.target, TpyName):
            self._mark_bound_loop_var_sources(stmt.target.name, target_type,
                                              stmt.value)

        # Mark as definitely assigned for plain name targets
        if isinstance(stmt.target, TpyName):
            self.init.mark_assigned(stmt.target.name)
            self.narrowing.update_after_write(stmt.target.name, target_type, value_type, stmt.value)
        elif isinstance(stmt.target, TpyFieldAccess):
            self.narrowing.invalidate_for_field_write(
                stmt.target, target_type, value_type, stmt.value)

    def _analyze_del_item(self, stmt: TpyDelItem) -> None:
        """`del t[k]`: the explicit `t.__delitem__(k)` call, a container
        literal's taking its preparation (`prepare_container_call`)."""
        for subscript in stmt.targets:
            subscript.reset_dunders()
            # The container is named as a consumer of its open cells: what
            # the delete does with its part is the stub's declared effect.
            with self.pend.list_sink(subscript.obj):
                self.expr.analyze_expr(subscript.obj)
            # Track mutation of for-each loop variables and parameters
            # del item removes an element from the container -- structural mutation.
            mark_roots(self.ctx, storage_roots(
                subscript.obj, Use.POINTEE, self.ctx.get_expr_type,
                MayLend(self.ctx)),
                MarkRequest(loop_vars=True, through_field=True,
                            structural=True))
            # Borrow conflict: del on a container with element-level borrows
            storage = self._resolve_obj_storage(subscript.obj)
            if storage is not None:
                bt = self.ctx.func.borrow_tracker
                if bt.has_element_borrow(storage):
                    self.ctx.warning(storage_loan_mutation_warning(
                        bt, storage, "'del'"), stmt)
                self.ctx.mark_all_view_borrowers_mutated(storage)
            self._enforce_readonly_assignment_target(subscript)
            obj_type = self.ctx.get_expr_type(subscript.obj)
            if isinstance(unwrap_readonly(obj_type), TupleType):
                raise self.ctx.error(
                    "Tuples are immutable; cannot delete tuple elements", stmt)
            recv = self._subscript_receiver(subscript)
            record = (self.ctx.registry.get_record_for_type(recv[0])
                      if recv is not None else None)
            if record is None or not self.expr.operators.dunder_overloads(
                    record, SubscriptDunder.DEL.value):
                raise self.expr.no_subscript_dunder(
                    recv[0] if recv is not None else obj_type,
                    SubscriptDunder.DEL, stmt)
            # No builtin declares a slice `__delitem__`
            # (BUGS.md#del-slice-rejected).
            if isinstance(subscript.index, TpySlice):
                raise self.ctx.error(
                    "deleting a slice ('del xs[a:b]') is not yet supported", stmt)
            assert recv is not None
            receiver, readonly = recv
            sv = self.expr.subscript_views(receiver, SubscriptDunder.DEL)
            key_type = self.expr.analyze_subscript_key(subscript, sv)
            if self.expr.methods.prepares(receiver):
                args = [subscript.index]
                prepared = self.expr.methods.prepare_container_call(
                    stmt, subscript.obj, receiver, SubscriptDunder.DEL.value,
                    args, evidence={0: key_type},
                    what=self.expr.describe_pending_use(subscript.obj))
                subscript.index = args[0]
                if prepared.args is not None and prepared.args[0] is not None:
                    key_type = prepared.args[0]
                sv = self.expr.subscript_views(
                    unwrap_readonly(unwrap_ref_type(prepared.receiver)),
                    SubscriptDunder.DEL)
            callee = self.expr.resolve_subscript_dunder(
                subscript, sv, readonly, [key_type])
            if callee.root.element_access is None:
                # `del t[k]` is the explicit `t.__delitem__(k)` call, whose
                # code may write anything.
                record_implicit_call(subscript, callee.root, subscript.obj,
                                     callee.name)

    def _write_target_receiver(self, target: TpyFieldAccess) -> TpyType | None:
        """The type of write target `target`'s receiver, analyzed ahead of
        the target; None for a receiver naming a module or a class, whose
        member the target's own analysis resolves."""
        if not self.expr.receiver_is_value(target.obj):
            return None
        return self.expr.analyze_expr(target.obj)

    def _target_is_dyn_writable_only(self, target: TpyFieldAccess,
                                     obj_type: TpyType) -> bool:
        """D16: True if `target` is `obj.foo` where foo is undeclared on the
        receiver's class AND the class has __setattr__ but not __getattr__.

        In that combination the read-side analyzer would raise "no field"
        before reaching the assign-time dunder routing, so the assign path
        needs to take over BEFORE calling analyze_expr on the target.
        """
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
        mark_roots(self.ctx, storage_roots(
            stmt.target.obj, Use.POINTEE, self.ctx.get_expr_type,
            MayLend(self.ctx)),
            MarkRequest(loop_vars=True, through_field=True))
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
            mark_roots(self.ctx, storage_roots(
                target.obj, Use.POINTEE, self.ctx.get_expr_type,
                MayLend(self.ctx)),
                MarkRequest(loop_vars=True, through_field=True))
            self._enforce_readonly_assignment_target(target)

    def _reject_generator_end_under_loop(self, name: str, node: TpyStmt,
                                         verb: str, remedy: str) -> None:
        """A running `for` over `name` steps the generator it holds, which
        a rebind (rebuilt in place) or a `del` (closed in place) would pull
        out from under it."""
        bt = self.ctx.func.borrow_tracker
        if bt.has_iter_borrow(StorageKey.of_name(name)) or bt.has_iter_borrow(
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
            declared = self._declared_binding(name)
            if (spelled is not None and declared is not None
                    and declared.is_value_type()):
                # Only reference types were ever deferred, so `del` of a
                # value under a pending return compiled; the eager capture
                # keeps that. Allowing it for references would change what
                # compiles.
                self._decline_deferred_return(name, stmt)
            elif spelled is not None:
                raise self.ctx.error(
                    f"cannot delete '{name}' in this finally block: an "
                    f"enclosed '{spelled}' still borrows it (the value "
                    f"is materialized after the finally runs)", stmt)
            # Global-declared and nonlocal vars are always reachable;
            # locals/params must be definitely assigned.
            is_external = (name in self.ctx.func.global_declarations
                           or name in self.ctx.func.current_nonlocal_names)
            if not is_external and name not in self.ctx.func.definitely_assigned:
                raise self.ctx.error(not_assigned_message(name), stmt)
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
            if (same_width_family(target_type, result_type)
                    and not is_integer_type(target_type)
                    and self.deduction.slot_is_declared(name)):
                self._refuse_declared_aug_widening(
                    name, target_type, result_type, op, stmt)
            if (is_integer_type(target_type) and is_integer_type(result_type)
                    and self.pend.cell_for(name) is None):
                # An integer local whose bindings no cell joins (a loop or
                # unpack target among them) is a slot of its own type: the
                # operation's result narrows into it, checked.
                effective_type = target_type
            else:
                effective_type = self.deduction.resolve_reassignment_target_type(
                    name, target_type, result_type, aug_op=op, site=stmt,
                )
            self.compat.warn_foreign_store_narrowing(
                name, result_type, effective_type, None, stmt)
            # check_type_compatible errors when effective_type refused widening
            # (e.g. annotated variable, or mixed-sign fixed-int pair).
            coercion = self.compat.check_type_compatible(
                result_type, effective_type, f"'{op}=' to '{name}'", loc=stmt.loc,
            )
            self._narrow_aug_result(stmt, result_type, effective_type,
                                    coercion, f"'{op}=' to '{name}'")
            if self.ctx.func.current_scope:
                self.deduction.join_rebind(
                    name, target_type, effective_type,
                    self.ctx.func.current_scope,
                    stmt.loc.line if stmt.loc else 0)
        else:
            # Subscript/field target: element type is fixed, cannot widen.
            what = f"'{op}=' to {_format_aug_target(target)}"
            coercion = self.compat.check_type_compatible(
                result_type, target_type, what, loc=stmt.loc,
                target_is_storage_form=True,
            )
            self._narrow_aug_result(stmt, result_type, target_type,
                                    coercion, what)

    def _narrow_aug_result(self, stmt: TpyAugAssign, result_type: TpyType,
                           target_type: TpyType, coercion: Coercion | None,
                           what: str) -> None:
        """An integer operation's result that narrows into the target
        (`x8 += 1` computes in the default int): the store takes it through
        the narrowing conversion (`TpyAugAssign.stored_value`). A record
        subscript's setter later replaces it with its own parameter's
        conversion (`_resolve_aug_store`), which narrows at the call."""
        if coercion is None or not coercion.narrows_checked:
            return
        stmt.stored_value = self.compat.coerce_expr(
            self._aug_result_stand_in(stmt, result_type), result_type,
            target_type, what, CoercionContext.ASSIGN,
            target_is_storage_form=not isinstance(stmt.target, TpyName))

    def _aug_result_stand_in(self, stmt: TpyAugAssign,
                             result_type: TpyType) -> TpyBinOp:
        """The operation's result of `target op= value`, which no node of the
        statement spells, as the node a store's conversion is put on."""
        stand_in = TpyBinOp(left=stmt.target, op=stmt.op, right=stmt.value,
                            resolved_binop=stmt.resolved_binop, loc=stmt.loc)
        self.ctx.set_expr_type(stand_in, result_type)
        return stand_in

    def _refuse_declared_aug_widening(
        self, name: str, target_type: TpyType, result_type: TpyType,
        op: str, stmt: TpyAugAssign,
    ) -> None:
        """Refuse `x op= e` whose operator widened a declared slot, which
        keeps its declared type. (An inferred local is refused
        by the reassignment rule, `refuse_wider_rebind`.)"""
        value = operand_spelling(stmt.value)
        convert = (f"convert explicitly: {name} = "
                   f"{python_type_name(target_type)}({name} {op} {value or '...'})")
        raise self.ctx.error(wider_store_message(
            name, target_type, result_type, convert, declared=True,
            aug_op=op, aug_value=stmt.value), stmt)

    def _is_non_owned_var_copy(self, stmt: TpyAssign, value_type: TpyType,
                               target_type: TpyType,
                               name_moved: bool) -> bool:
        """Check if storing a non-OwnType variable copies into storage.

        Covers locals excluded from OwnType wrapping: reassigned vars,
        union types, Optional with pointer repr.  These are not caught by
        the unified Ref/Own check. `name_moved` is the store's move verdict.
        """
        expr = stmt.value
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
        if scope_type is None:
            return False
        return not name_moved

    def _list_aug_target(
            self, stmt: TpyAugAssign, target_type: TpyType,
            value_type: TpyType,
    ) -> tuple[TpyType, ContainerCells | None]:
        """`c op= v` on a container literal whose cells decide its leaves,
        or an empty one a number seeds. An in-place operator whose one
        argument is entries the container then holds (`+=`, `|=`, `^=`;
        `ContainerCall.stores_elements`) stores each of them
        (`PendingNums.store_elements`): the operator is resolved at the
        parts known so far, and re-resolved once they settle. Any other
        decides the leaves first, as a use that needs them. Returns the
        target as the operator sees it and the cells of a store."""
        imethod = builtin_modules.AUGOP_TO_IMETHOD.get(stmt.op)
        into = self.pend.cell_container(target_type)
        # `+=` / `|=` / `^=` store the source's entries (the stub's declared
        # effect); any other in-place operator decides the leaves first.
        call = (container_call(self.ctx, root_of(into), imethod, 1)
                if imethod and into is not None else None)
        stores = call is not None and call.stores_elements
        return self.pend.store_elements_at(
            stmt.target, target_type, stmt.value if stores else None,
            value_type, stmt, self.expr.describe_pending_use(stmt.target))

    def _resolve_inplace_later(self, stmt: TpyAugAssign, target_type: TpyType,
                               value_type: TpyType,
                               cell: ContainerCells) -> None:
        """The in-place operator of a `+=` / `|=` resolved against a
        container whose leaves are not decided yet names their types known
        then. Once the cells settle, resolve it at their types: it is what
        the lowering reads the operator's slots from. A dict's or set's
        source is the container's own type there (the runtime converts its
        entries)."""
        operators = self.expr.operators
        imethod = builtin_modules.AUGOP_TO_IMETHOD.get(stmt.op)
        call = (container_call(self.ctx, cell.tree, imethod, 1)
                if imethod else None)
        same = call is not None and not call.source_is_protocol

        def record(elem: TpyType) -> None:
            target = with_container(target_type, elem)
            result = self.expr.decide_operator(operators.resolve_aug_inplace(
                target, stmt.op, target if same else value_type,
                loc_node=stmt), stmt)
            if result is not None:
                stmt.resolved_inplace = result

        self.pend.when_elem_known(stmt, cell, record)

    def _receive_aug_operand(self, stmt: TpyAugAssign,
                             result: ResolvedBinop,
                             value_type: TpyType) -> bool:
        """`receive_dunder_operand` at `target op= value`: the value,
        converted in place. Whether it was received; a reflected dunder's
        operand is the target, which keeps its own render."""
        received = (None if result.is_reverse
                    else self.expr.receive_dunder_operand(
                        stmt.value, value_type, result))
        if received is None:
            return False
        stmt.value = received
        # The target's write is the statement's own.
        self.expr.calls.commit_facts(
            stmt, result.method, CallOperands(result.method, None, [received]))
        return True

    def _pending_aug_assign(self, stmt: TpyAugAssign, target_type: TpyType,
                            value_type: TpyType,
                            elem_cell: PendingNumCell | None = None) -> None:
        """`x op= v` with a pending number on either side, or into a
        literal-seeded local. That target stores the result as by any store
        (`x = x op v`), which is never a literal store: a literal operand
        adapts inside the operator. The operator is chosen once both sides
        have settled."""
        op = op_spelling(stmt.op)
        target, value = strip_int(target_type), strip_int(value_type)
        name = stmt.target.name if isinstance(stmt.target, TpyName) else None
        cell = self.pend.cell_for(name) if name is not None else elem_cell
        if cell is not None:
            if value_family(value) is None:
                raise self.ctx.error(
                    f"Augmented assignment value must be a numeric type, "
                    f"got {value}", stmt)
            # The value stored is the operation's, typed as a binary
            # operation over a pending operand is.
            result = self.expr.pending_binop_result(
                stmt.op, self.pend.cell_type(cell), value, stmt)
            if result is None:
                raise self.ctx.error(
                    f"Operator '{op}=' is not supported between "
                    f"{self.pend.known_so_far(cell)} and {value}", stmt)
            if elem_cell is not None:
                # The element takes the operation's result, as a store.
                self.pend.elem_store(cell, result, None, stmt)
            else:
                if value_family(strip_int(result)) != cell.is_float:
                    self.deduction.refuse_int_float_rebind(
                        name, self.pend.known_so_far(cell), strip_int(result),
                        None, aug_op=op, site=stmt)
                self.pend.add_store(cell, result, stmt)
                self.ctx.func.value_ranges.pop(name, None)
        elif not (is_any_int_type(target) or is_float_type(target)):
            raise self.ctx.error(
                f"Operator '{op}=' is not supported for {target_type}", stmt)
        elif name is not None:
            self.ctx.func.value_ranges.pop(name, None)
        pending_target = cell is not None
        self.pend.defer(stmt, (target, value),
                        lambda ts: self._resolve_numeric_aug(
                            stmt, *ts, seeded_target=pending_target,
                            elem_target=elem_cell is not None))
        if name is not None:
            self._decline_deferred_return(name, stmt)

    def _resolve_numeric_aug(self, stmt: TpyAugAssign, target: TpyType,
                             value: TpyType, seeded_target: bool,
                             elem_target: bool = False) -> None:
        """The operator of a numeric `x op= v` and its write-back, at the
        analysis or, over a pending side, once it settled. A literal-seeded
        target recorded the result as a store; any other keeps its type."""
        # The operation runs at the operands' own types, as `x = x op v`
        # does; only its result narrows into a fixed-width target.
        result = self.expr.decide_operator(self.expr.operators.resolve_binop(
            target, stmt.op, value, loc_node=stmt), stmt)
        if result is None:
            raise self.ctx.error(
                f"Operator '{op_spelling(stmt.op)}=' is not supported between "
                f"{target} and {value}", stmt)
        stmt.resolved_binop = result
        if seeded_target:
            self.pend.check_late_result(stmt, f"'{op_spelling(stmt.op)}='",
                                        result.method.return_type, target)
        if elem_target or not seeded_target:
            self._apply_aug_assign_writeback(
                stmt.target, target, result.method.return_type, stmt.op, stmt)

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.). On a record
        subscript `t[k] op= v` is `t.__setitem__(k, t.__getitem__(k) op v)`:
        the target read resolved its getter, the store resolves its setter
        once the operation is (`_resolve_aug_store`)."""
        if isinstance(stmt.target, TpySubscript):
            # The getter and the setter both read the key: neither read is
            # the one an owning slot may move (BUGS.md#subscript-key-arg-temp).
            self.ctx.reread_reads.add(peel_value_wrappers(stmt.target.index))
        self._analyze_aug_assign_body(stmt)
        if isinstance(stmt.target, TpySubscript):
            self._resolve_aug_store(stmt)
        stmt.hold_parts = aug_hold_parts(self.ctx, self.compat, stmt)

    def _resolve_aug_store(self, stmt: TpyAugAssign) -> None:
        """The `__setitem__` of a subscript `t[k] op= v`: ranked on the key
        as written and the operation's result (the getter's type where the
        operator settles later, as the write-back holds the result to it),
        the key received at it apart from the getter's
        (`TpySubscript.written_index`), and credited on the receiver."""
        target = stmt.target
        assert isinstance(target, TpySubscript)
        recv = self._subscript_receiver(target)
        if recv is None or target.written_index is None:
            return
        rb = stmt.resolved_binop
        result = (rb.method.return_type if rb is not None
                  else self.ctx.get_expr_type(target))
        key = target.written_index
        # A written slice has no type of its own: the slice it builds.
        key_type = self._written_key_type(target)
        assert result is not None
        result = unwrap_ref_type(result)
        # The setter receives the operation's result: its stand-in goes
        # through the call's receiving step.
        value: list[TpyExpr] = [self._aug_result_stand_in(stmt, result)]
        receiver = recv[0]
        lc = self.pend.container_cells(receiver)
        if lc is not None:
            # A container literal's setter is resolved at the receiver its
            # getter's call saw (`MethodAnalyzer.prepare_container_call`):
            # its parts as the cells have them, as the operation's result
            # has them too.
            receiver = self.pend.receiver_form(receiver, lc)
        getter_key = target.index
        target.index = key
        self.ctx.reread_reads.add(peel_value_wrappers(key))
        try:
            self.expr.resolve_subscript_dunder(
                target, self.expr.subscript_views(
                    receiver, SubscriptDunder.SET),
                recv[1], [key_type, result], value)
        finally:
            target.setitem_index, target.index = target.index, getter_key
        stmt.stored_value = value[0]
        storage = self._resolve_obj_storage(target.obj)
        hit = (self.ctx.func.borrow_tracker.element_hop_loan(
                   storage, element_index_key(key))
               if storage is not None else None)
        self._credit_subscript_setitem(target, stmt, check_loans=hit is None)

    def _analyze_aug_assign_body(self, stmt: TpyAugAssign) -> None:
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
        # The target of a store is no use of a pending local, or of the
        # element of a list literal; a list's own `+=` may be a store into it.
        with self.pend.sink(stmt.target
                            if isinstance(stmt.target, (TpyName, TpySubscript))
                            else None), \
                self.pend.list_sink(stmt.target
                                    if isinstance(stmt.target, TpyName)
                                    else None):
            target_type = unwrap_own(unwrap_ref_type(
                self.expr.analyze_expr(stmt.target)))
        list_target = (isinstance(stmt.target, TpyName)
                       and self.pend.cell_container(target_type) is not None)
        elem_cell = self._target_elem_cell(stmt.target)
        # Augmented assignment on properties not yet supported
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.resolved_property_getter is not None:
            raise self.ctx.error(
                f"Augmented assignment on property '{stmt.target.field}' is not yet supported",
                stmt,
            )
        self._check_constant_write(stmt.target, stmt)
        # Aug-assign replaces the target's value with a freshly computed one
        # (owned str/bytes concat, reallocated list, etc.), so any prior
        # param-derived / safe-to-return provenance is now stale and must be
        # cleared -- otherwise a later `return` as a view would pass the
        # dangling check despite pointing into local storage.
        if isinstance(stmt.target, TpyName) and _needs_provenance_tracking(target_type):
            self.init.mark_provenance(stmt.target.name, False)
            self.init.mark_safe_to_return(stmt.target.name, False)
        # `x op= v` into a local or a numeric field is a store a pending
        # integer reaches; a literal-seeded target is no declared slot to
        # hint the value with.
        seeded_target = elem_cell is not None or (
            isinstance(stmt.target, TpyName)
            and self.pend.cell_for(stmt.target.name) is not None)
        numeric_store = seeded_target or (
            isinstance(stmt.target, (TpyName, TpyFieldAccess))
            and is_numeric_slot(target_type))
        # A dict's or set's source is read open: its nested containers are
        # linked to the target's before its numbers settle
        # (`PendingNums.store_elements`).
        open_source = (list_target and not isinstance(
            root_of(self.pend.cell_container(target_type)), PendingListType))
        with self.pend.sink(stmt.value if numeric_store else None), \
                self.pend.list_sink(stmt.value if open_source else None):
            value_type = (self.expr.analyze_expr(stmt.value)
                          if seeded_target or list_target
                          else self.expr.analyze_expr_with_hint(stmt.value, target_type))
        inplace_cell: ContainerCells | None = None
        if list_target:
            target_type, inplace_cell = self._list_aug_target(
                stmt, target_type, value_type)
            if open_source:
                value_type = self.expr._pending_gate(stmt.value, value_type)
            imethod = builtin_modules.AUGOP_TO_IMETHOD.get(stmt.op)
            call = (container_call(self.ctx, inplace_cell.tree, imethod, 1)
                    if inplace_cell is not None and imethod else None)
            if call is not None and not call.source_is_protocol:
                # The source's entries went in; the operator takes the
                # container's own type (the runtime converts them).
                value_type = target_type
            else:
                stored = self.pend.list_cells(value_type)
                if stored is not None:
                    value_type = self.pend.so_far(value_type, stored)
        # Track mutation of for-each loop variables and parameters
        # items += other_list extends the container in-place (structural mutation).
        # items[i] += x and obj.field += x are in-place element/field writes -- not structural.
        mark_roots(self.ctx, storage_roots(stmt.target, Use.PLACE,
                                           self.ctx.get_expr_type,
                                           MayLend(self.ctx)),
                   MarkRequest(loop_vars=True, through_field=True,
                               structural=isinstance(stmt.target, TpyName)))
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
        elif isinstance(stmt.target, TpyFieldAccess):
            storage = self._resolve_obj_storage(stmt.target.obj)
            field_storage = storage_key(stmt.target)
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
                self.ctx.warning(storage_loan_mutation_warning(
                    bt, storage, "field assignment"), stmt)
            self._mark_field_write_views(stmt.target, storage, field_storage)
        # Borrow conflict: aug-assign on a name target that has element borrows.
        # Any structural aug-assign (list +=, set |=, user-defined __iadd__ that
        # reallocates) is a mutation -- check the borrow state, not the container type.
        elif isinstance(stmt.target, TpyName):
            bt = self.ctx.func.borrow_tracker
            storage = bt.effective_storage(stmt.target.name)
            if bt.has_element_borrow(storage):
                self.ctx.warning(storage_loan_mutation_warning(
                    bt, storage, f"'{op_spelling(stmt.op)}='"), stmt)
            self.ctx.mark_all_view_borrowers_mutated(storage)
            # Aug-assign reallocates the buffer just as a rebind does, so it
            # invalidates pinned views of this name (symmetric with plain assign).
            _handle_pinned_view_rebind(self.ctx, stmt.target.name, stmt)
        # Literal-typed targets reject augmented assignment outright: the result
        # of `x += y` is rarely in the declared value set, and Literal[str] locals
        # use std::string_view storage which can't hold a new owned string anyway.
        if isinstance(target_type, LiteralType):
            raise self.ctx.error(
                f"Augmented assignment is not supported for Literal[...] -- the "
                f"result is not guaranteed to be in the declared value set",
                stmt,
            )
        # A literal-seeded target, settled or not, takes the result as a
        # store: it widens the local or is checked against its settled type.
        if seeded_target or is_pending_num(value_type):
            self._pending_aug_assign(stmt, target_type, value_type, elem_cell)
            return
        # Target must be numeric, owned string, or a type with registered operators.
        # StrView is excluded -- it's non-owning, so += would dangle.
        is_numeric_target = is_any_int_type(target_type) or is_float_type(target_type)
        is_str_target = (is_str_type(target_type) or is_string_type(target_type)
                         or isinstance(target_type, PendingStrType))
        is_bytes_target = (is_bytes_type(target_type)
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
            if result := self.expr.decide_operator(operators.resolve_aug_inplace(
                target_type, stmt.op, value_type, loc_node=stmt,
            ), stmt):
                stmt.resolved_inplace = result
                if inplace_cell is not None and contains_pending_leaf(
                        self.pend.tree_type(inplace_cell)):
                    self._resolve_inplace_later(stmt, target_type,
                                                value_type, inplace_cell)
                if self._receive_aug_operand(stmt, result, value_type):
                    return
                if result.method.params:
                    _, param_type = result.method.params[0]
                    self.compat.check_type_compatible(
                        value_type, param_type, f"'{op_spelling(stmt.op)}=' operand", source_expr=stmt.value,
                    )
                return
            # Method exists but arg type mismatches -- produce a specific type error.
            expected_params = operators.get_aug_inplace_param_types(target_type, stmt.op)
            if len(expected_params) == 1:
                self.compat.check_type_compatible(
                    value_type, expected_params[0], f"'{op_spelling(stmt.op)}=' operand",
                    loc=stmt.loc, source_expr=stmt.value,
                )
            elif expected_params and not any(
                    self.compat.is_type_compatible(value_type, p)
                    for p in expected_params):
                expected_str = " or ".join(user_type_name(unwrap_ref_type(p))
                                           for p in expected_params)
                raise self.ctx.error(
                    f"Type mismatch in '{op_spelling(stmt.op)}=' operand: "
                    f"expected {expected_str}, got {user_type_name(value_type)}",
                    stmt)
            if result := self.expr.decide_operator(operators.resolve_binop(
                    target_type, stmt.op, value_type, loc_node=stmt), stmt):
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
                self._receive_aug_operand(stmt, result, value_type)
                if isinstance(stmt.target, TpyName):
                    # `x += v` through `__add__` is `x = x + v`: the
                    # reassignment rules of a parameter, a loop variable
                    # and a global hold for it.
                    self._check_nonvalue_rebinding(stmt.target.name, stmt)
                    in_place = self._check_fallback_rebinds_owner(
                        stmt, result.method.name, target_type)
                    # `__add__` REBINDS the name: a pending deferred return
                    # of it must keep the object it named, so its capture
                    # goes back to eager, as for a plain rebind.
                    self._decline_deferred_return(stmt.target.name, stmt)
                    if in_place is not None:
                        self.ctx.warning(in_place, stmt)
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
        # Invalidate range facts for the target (value has changed)
        if isinstance(stmt.target, TpyName):
            self.ctx.func.value_ranges.pop(stmt.target.name, None)
        if is_numeric_target:
            self._resolve_numeric_aug(stmt, target_type, check_value_type,
                                      seeded_target=False)
        elif result := self.expr.decide_operator(self.expr.operators.resolve_binop(
                target_type, stmt.op, check_value_type, loc_node=stmt), stmt):
            stmt.resolved_binop = result
