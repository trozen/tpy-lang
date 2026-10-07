"""
TurboPython Type Compatibility

Type compatibility checking, coercions, and lvalue analysis.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, Optional

from ..typesys import (
    ELEM,
    TpyType, IntLiteralType, FloatLiteralType, ListRepeatType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingBytesType, PendingViewType, UnknownElementType,
    view_family_for_type,
    LiteralType, LiteralValue, LiteralTag, FLOAT, make_list, make_dict, make_set,
    OwnType, ReadonlyType, VoidType, PtrType, is_readonly_ptr, TupleType,
    NominalType, AliasRef, RecursiveAliasInstanceType, TypeParamRef, TypeParamKind, NoneType, AnyType, OptionalType, UnionType,
    is_protocol_type, is_dyn_protocol, unwrap_own, strip_own_type_args, unwrap_readonly, unwrap_optional_own,
    contains_type_param,
    is_any_str_type, get_covariant_params, PendingGenericInstanceType,
    CallableType, is_fn_type, RefType, unwrap_ref_type,
    is_callable_type, is_integer_type, is_any_float_type, is_readonly_span,
    unify_literal_types,
    is_polymorphic_class_type, is_exception_type, SendType, SyncType, unwrap_send_sync, FrameType,
    unwrap_qualifiers,
    disambiguated_pair, ConcreteCoroType, recorded_return_borrow_sources,
    GenExprType, is_open_type_param_return, PendingNumType)
from .. import qnames
from ..value_category import (
    CONTAINER_LITERAL_NODES, async_result_aliases, call_returns_cpp_ref,
    is_iterator_protocol, is_rvalue_source,
    peel_value_wrappers,
    property_access_returns_cpp_ref, returns_borrow)
from . import own_copy
from .type_join import user_type_name
from .frame_traits import frame_traits_of_function, frame_type_of_function
from .send_chain import why_not_send, why_not_sync, why_not_frame, render_chain
from .move_chain import why_not_movable, render_move_chain
from ..parse import (
    ResultForm,
    TpyExpr, TpyName, TpyFieldAccess, TpySubscript, TpyArrayLiteral,
    TpyDictLiteral, TpySetLiteral, TpyListRepeat, TpyCall, TpyCallLike, TpyMethodCall, TpyUnaryOp,
    TpyBinOp, TpyCoerce, TpyNoneLiteral, TpyIntLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFunction, TpyIfExpr, TpyTupleLiteral, TpyLambda, TpyNamedExpr, TpyFString,
    lambda_of,
    TpyAwait, TpyGeneratorExpression, SourceLocation
)
from .literal_utils import literal_value_from_expr
from ..coercions import resolve_coercion, borrow_only_veto, Coercion, CoercionContext, DEREF_COERCION, UPCAST_TO_PTR, UPCAST_TO_CONST_PTR, SPAN_METHOD_TO_SPAN_ARG, SPAN_METHOD_TO_SPAN, INTO_ANY, FROM_ANY, literal_range_error
from ..modules import get_span_return_type
from .context import OwnSlot, addr_taken_roots, _storage_root, BorrowKind, BorrowTracker, CallOperands, LendSource, call_borrow_operands, call_lend_sources, tuple_borrow_escape_roots
from .numeric_lattice import fixed_int_range_contains, numeric_info
from ..diagnostics import (
    SemanticError, NOCOPY_REMEDIATION_HINT, CONSUMING_FIELD_MOVE_NOTE,
    CONSUMING_FIELD_COPY_CLAUSE)
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_varargs, is_span_iter, is_list,
    is_str_view_type, is_bytes_view_type, is_borrowing_view_type, int_traits_of,
    is_big_int_type, is_str_category, is_bytes_category, is_str_type, is_string_type,
    is_bytes_type, is_bytearray_type,
    protocol_info_of, iter_yields_owned_elements, is_iterator_adapter,
    is_dict_view,
)
from .overloads import type_matches_numeric
from .pending_num import (ContainerCells, at_path, container_parts,
                          is_numeric_slot, is_pending_num, literal_entries,
                          map_leaves,
                          pending_list_of, root_of, strip_int,
                          value_family, value_leaves, with_container)
from .iter_loans import iteration_copies_lent_reference, iteration_lend_pending


def _literal_value_from_source(
    source_expr: TpyExpr | None, expected: LiteralType,
) -> LiteralValue | None:
    """LiteralValue from a literal AST source, gated on tag-vs-base agreement."""
    lv = literal_value_from_expr(source_expr)
    if lv is None:
        return None
    if lv.tag is LiteralTag.STR and expected.is_str_base():
        return lv
    if lv.tag is LiteralTag.BOOL and expected.is_bool_base():
        return lv
    if lv.tag is LiteralTag.INT and expected.is_int_base():
        return lv
    return None


# Wording for the borrowing-view families that have an owning type to switch
# to: the display name and those owning types. Which types ARE borrowing views
# is `is_borrowing_view_type`; this table only picks the message. Ordered: a
# *args pack is also a Span, so the Span arm must be tried first to keep its
# wording.
_VIEW_RETURN_FAMILIES = (
    (is_str_view_type, "StrView", "str or String"),
    (is_bytes_view_type, "BytesView", "bytes or bytearray"),
    (is_span, "Span", "list or Array"),
    (is_varargs, "a *args view", "list or Array"),
)


def _view_return_family(return_type: TpyType) -> tuple[str, str] | None:
    """(display name, owning alternatives) for a borrowing-view return type."""
    for pred, display, owned in _VIEW_RETURN_FAMILIES:
        if pred(return_type):
            return display, owned
    return None


def _view_keeping_hint(name: str, display: str, source: str | None) -> str:
    """The two spellings that keep a view of stored storage: returning the
    source itself, or a local spelled as a view."""
    if source is not None:
        # The explicit `{name}: {display} = {source}` opt-in is not named: a
        # spelled view local takes no loan today
        # (BUGS.md#explicit-view-local-source-mutation-unguarded), so the
        # compiler must not steer a user to it.
        return (f"'{name}' owns a copy of '{source}'; return {source} "
                f"directly")
    return (f"'{name}' owns a copy of its source; return the source "
            f"directly")


def _dangling_view_message(return_type: TpyType) -> str | None:
    """Error message for returning a view that borrows from a local, or None
    if return_type is not a borrowing-view type.
    """
    if not is_borrowing_view_type(return_type):
        return None
    family = _view_return_family(return_type)
    if family is not None:
        display, owned = family
        return (f"Cannot return {display} referencing a local or temporary; "
                f"use {owned} to return an owned copy")
    if is_span_iter(return_type):
        return ("Cannot return SpanIter referencing a local or temporary; "
                "the underlying Span would dangle after the function returns")
    display = return_type.name if isinstance(return_type, NominalType) else str(return_type)
    return (f"Cannot return {display} referencing a local or temporary; "
            f"the storage it borrows would dangle after the function returns")


def _elem_is_borrow_form(elem_type: TpyType) -> bool:
    """Whether a tuple element's slot holds a BORROW of its source rather than
    a copy of it -- the element-level mirror of `check_dangling_reference`'s
    own arms (a pointer, a view over the source's buffer, a reference form).

    A value element is COPIED into the tuple, so nothing that happens to its
    source afterwards can reach it; the scalar return of the same expression
    takes `check_dangling_reference`'s value-type early return and is not
    borrow-checked either, so the two must agree here. A type-parameter
    element is treated as a copy: at an open `T` the view family is unknown,
    and every generic tuple-return shape rejects at lowering before either
    rule sees it, so nothing is lost by the conservative reading.
    """
    bare = unwrap_readonly(elem_type)
    if isinstance(bare, (OwnType, TypeParamRef)):
        return False
    if isinstance(bare, PtrType) or _dangling_view_message(bare) is not None:
        return True
    return not bare.is_value_type()


def _container_elem_matches(actual_elem: TpyType, expected_elem: TpyType) -> bool:
    """Strict element type check for container assignment.

    Allows Own[T] stripping and numeric literal coercions only.
    Subclass coercion is excluded: C++ containers are non-converting templates
    (invariant T) -- set[Child] cannot be used where set[Base] is expected.
    """
    check = expected_elem.wrapped if isinstance(expected_elem, OwnType) else expected_elem
    return actual_elem == check or type_matches_numeric(actual_elem, check)


def _contains_semantic_ref(t: TpyType) -> bool:
    """True if t contains a RefType wrapper (explicit borrowed reference).

    Unlike `own_copy.contains_reference_type`, which asks the structural
    is_value_type() question, this checks for RefType specifically -- the
    semantic marker that sema inserts for borrowed references. Used to detect
    iterator-to-container copies where the source is an rvalue but yields
    borrowed elements.
    """
    if isinstance(t, RefType):
        return True
    return any(_contains_semantic_ref(inner) for inner in t.inner_types())


def _inner_compat_with_ro_widening(source_inner: TpyType, dest_inner: TpyType) -> bool:
    """True when `source_inner` is compatible with `dest_inner` at a Ptr <-> Optional
    pointer-repr boundary, allowing mutable->readonly inner widening only.

    Used for both `Ptr[T] -> T | None` (source_inner = ptr pointee, dest_inner =
    optional inner) and `T | None -> Ptr[T]` (source_inner = optional inner,
    dest_inner = ptr pointee). The two directions are complementary: in each,
    `source_inner` is on the source side and `dest_inner` is on the destination
    side, regardless of which side is the Ptr.

    - Same inner: always allowed.
    - Mutable source -> readonly destination: allowed (adding const is safe).
    - Readonly source -> mutable destination: rejected (dropping const is unsafe).
    """
    if source_inner == dest_inner:
        return True
    if isinstance(dest_inner, ReadonlyType) and not isinstance(source_inner, ReadonlyType):
        return dest_inner.wrapped == source_inner
    return False


def _ptr_readonly_compatible(actual: PtrType, expected: PtrType) -> bool:
    """True if `actual`'s readonly-ness may flow into `expected` -- a
    mutable target from a readonly source would launder away the const."""
    return expected.is_readonly or not actual.is_readonly


def _is_natural_union_member(actual: TpyType, a_info, member: TpyType) -> bool:
    """True if `member` is the natural target for `actual` in a union match,
    i.e. selecting it does not require a category-crossing widening.

    `a_info` must be `numeric_info(actual)` (passed in to avoid recomputing).
    """
    if member == actual:
        return True
    m_info = numeric_info(member)
    if a_info is None or m_info is None:
        return False
    if a_info.family == m_info.family:
        return True
    # Literal families naturally land on their concrete counterpart.
    if a_info.family == "int_literal" and m_info.family == "int":
        return True
    if a_info.family == "float_literal" and m_info.family == "float":
        return True
    return False


def _union_member_order(actual: TpyType,
                    members: 'tuple[TpyType, ...]') -> list[TpyType]:
    """The members a value is tried against, most specific first.

    Canonical member order is a spelling artifact (make_union sorts on
    the display name), so it must not decide where a value lands. The
    member equal to the value's type wins, then the natural (same-
    family) members with a fixed width before BigInt, then the
    category-crossing widenings; ties keep canonical order. A literal
    renders bare and the C++ variant's converting constructor picks
    its alternative, so its ranking mirrors that rule: an `int`
    literal converts without narrowing only to a signed width of at
    least 32 bits that holds it (int32, then int64), everything else
    loses to BigInt; a float literal is a double, so `float` wins.
    """
    a_info = numeric_info(actual)
    natural: list[TpyType] = []
    widening: list[TpyType] = []
    for member in members:
        if _is_natural_union_member(actual, a_info, member):
            natural.append(member)
        else:
            widening.append(member)
    literal_value = actual.value if isinstance(actual, IntLiteralType) else None

    def rank(member: TpyType) -> int:
        if member == actual:
            return 0
        m_info = numeric_info(unwrap_readonly(member))
        if literal_value is not None and m_info is not None and m_info.family == "int":
            inner = unwrap_readonly(member)
            if is_big_int_type(inner):
                return 3
            tr = int_traits_of(inner)
            if (tr is None or not tr.signed or tr.bits < 32
                    or not fixed_int_range_contains(inner, literal_value)):
                return 4
            return 1 if tr.bits == 32 else 2
        if isinstance(actual, FloatLiteralType):
            return 1 if unwrap_readonly(member) == FLOAT else 2
        if m_info is not None and m_info.family == "int" and is_big_int_type(unwrap_readonly(member)):
            return 2
        return 1

    natural.sort(key=rank)
    return natural + widening


class TupleSink(Enum):
    """Where a tuple literal lands, which decides each member's copy rule.

    A member gets the rule a scalar of its type gets at the same slot: an
    `Own`-marked member takes the sink's `Own` rule, an unmarked reference
    member is a borrow (it aliases) at the RETURN / ARG / YIELD / LOCAL
    sinks and owned storage at FIELD / CONTAINER. A member of a NESTED value
    tuple is owned storage at every sink that stores one."""
    RETURN = auto()
    ARG = auto()
    YIELD = auto()
    LOCAL = auto()
    FIELD = auto()
    CONTAINER = auto()


_BORROW_FORM_SINKS = frozenset({TupleSink.RETURN, TupleSink.ARG,
                                TupleSink.YIELD, TupleSink.LOCAL})
# The sinks whose codegen MOVES a last-use owned member into the slot; the
# others lift the literal through `tuple_to_storage`, which copies even then.
_LAST_USE_MOVES_SINKS = frozenset({TupleSink.FIELD, TupleSink.YIELD})


def _declared_call_member(m: TpyExpr) -> bool:
    """A tuple-literal member that is a borrow-declared call handing back
    an operand (`(d.get(k, fb), 1)`): a LOCAL holds it by value, not by
    pointer -- its element has no pointer render yet (TODO.md "Sink capture
    keys on the shared lending verdict") -- so it is owned storage there
    and takes that slot's copy rule."""
    inner = peel_value_wrappers(m)
    return isinstance(inner, TpyCallLike) and inner.result_form.from_operand


class CompatError:
    """Type compatibility check failure (returned by _check_compat, not raised)."""
    __slots__ = ('message', 'loc')

    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc


# Result type for _check_compat: Coercion | None (compatible) or CompatError (incompatible)
CompatResult = Coercion | CompatError | None


if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .methods import MethodAnalyzer
    from .local_deduction import LocalTypeDeduction
    from .pending_num import PendingNums
    from ..typesys import FunctionInfo


class PendingIterCopyCheck(NamedTuple):
    """An element-copy check on an iterator source whose lending callee's
    borrow facts were still pending when the copy was analyzed."""
    source_expr: TpyExpr
    elem_type: TpyType
    display_type: TpyType
    hint: str
    loc: SourceLocation | None


def _handle_lends_elements(t: TpyType) -> bool:
    """Whether a value of type `t` is a handle that hands out elements it
    does not own: an iterator (a combinator, a generator frame or
    expression, a Span/view iterator) or a dict view.

    An allow-list: every such handle lends unless its type PROVES the
    elements owned (`iter_yields_owned_elements`, an `Own[T]` element). A
    handle whose provenance is temporaries only is answered by its
    binding's init, not here (`_dead_handle_value`)."""
    bare = unwrap_ref_type(unwrap_readonly(unwrap_own(unwrap_send_sync(t))))
    if isinstance(bare, GenExprType):
        elem: TpyType | None = bare.element_type
    elif is_iterator_protocol(bare):
        elem = bare.type_args[0] if bare.type_args else None
    elif is_iterator_adapter(bare) or is_dict_view(bare):
        elem = None
    else:
        return False
    if iter_yields_owned_elements(bare):
        return False
    return not (elem is not None
                and isinstance(unwrap_readonly(elem), OwnType))


class TypeCompatibility:
    """Type compatibility checking, coercions, and lvalue analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # Set after construction (compat is created before these exist)
        self.type_ops: TypeOperations
        self.protocols: ProtocolChecker
        self.methods: MethodAnalyzer
        self.deduction: 'LocalTypeDeduction'
        self.pend: 'PendingNums'
        self.pending_iter_copy_checks: list[PendingIterCopyCheck] = []

    def resolve_pending_iter_copy_checks(self) -> None:
        """Decide the element-copy checks deferred on a pending callee, now
        that every body's borrow facts are final -- so the verdict does not
        depend on whether the generator is defined above or below its use."""
        for check in self.pending_iter_copy_checks:
            if iteration_copies_lent_reference(
                    self.ctx, check.source_expr, check.elem_type):
                self.ctx.warning_from_loc(
                    f"copies {check.display_type} elements; {check.hint}",
                    check.loc)
        self.pending_iter_copy_checks.clear()

    def _mark_addr_taken(self, expr: TpyExpr) -> None:
        """Mark all param roots of expr as mutated because their address is taken.

        Resolves alias / element / field / ptr borrow chains via
        `effective_storage` so element-borrowing aliases (`v = items[i]`)
        trace back to their underlying param. Loop variables also propagate
        to their source iterables -- both are storage that the address-take
        could mutate via the resulting pointer.
        """
        for name in addr_taken_roots(expr):
            root = self.ctx.func.borrow_tracker.effective_storage(name)
            self.ctx.mark_param_mutated(root)
            self.ctx.mark_loop_var_mutated(root)

    def is_type_compatible(self, actual: TpyType, expected: TpyType,
                           coercion_ctx: CoercionContext | None = None) -> bool:
        """Non-raising check: is actual assignable to expected (in
        `coercion_ctx`: an argument converts as one does)? A query: it
        writes nothing -- no container literal's element or storage, no
        pending generic instance -- so an overload candidate can ask it and
        lose without leaving a trace (only `commit` writes)."""
        return not isinstance(
            self._check_compat(actual, expected, "", coercion_ctx=coercion_ctx),
            CompatError)

    def _list_at_container(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None, source_expr: TpyExpr | None,
        coercion_ctx: CoercionContext | None, commit: bool,
    ) -> 'TpyType | CompatError | None':
        """A list literal checked against a slot that holds a typed
        container of numbers (`PendingNums.slot_containers`). One whose
        element a cell decides may be confirmed by the container or widened
        within its family: returns the list as the container sees it, the
        refusal, or None when `expected` holds no such container. The
        element is DECIDED here only under `commit` -- the check that
        produces the coercion, outside an overload trial; any other gets
        the verdict alone. A literal with no cell adapts to the container,
        so each number written in it must fit the container's element."""
        into = self.pend.cell_container(actual)
        if into is None:
            if pending_list_of(actual) is None:
                return None
            return self._literal_values_fit(actual, expected, context, loc)
        # A declared view converts each element as it reads it and decides
        # nothing about the list; a generic call's resolved one does.
        scope = (self.ctx.call_scope_of(into.cells)
                 if isinstance(into, ContainerCells) else None)
        adaptive = scope is not None and scope.resolves(source_expr)
        verb = coercion_ctx.verb if coercion_ctx is not None else "stored"
        container, shown, refusal = self.pend.meets_list(
            into, expected, verb, adaptive, self.member_order(actual))
        if refusal is not None:
            return CompatError(refusal, loc)
        if container is None:
            return None
        if commit and not self.ctx.trial_depth:
            decided = self.pend.decide_list(actual, into, container,
                                            source_expr, verb, shown,
                                            declared=not adaptive)
            if decided is not None:
                return decided
        if isinstance(into, ContainerCells):
            # The leaves as the container has them; a part that holds no
            # number stays the literal's, for the ordinary check to judge.
            return with_container(actual, map_leaves(
                into.tree, lambda path, _leaf: at_path(container, path)))
        root = root_of(into)
        return with_container(actual, root.with_parts(
            container_parts(self.pend.context_elem(container), root)))

    def list_at_slot(self, actual: TpyType, expected: TpyType,
                     source_expr: TpyExpr,
                     coercion_ctx: CoercionContext | None = None) -> TpyType:
        """A select operand, bare or a tuple literal, that hands a list
        literal over undecided: a value the declared slot `expected`
        receives, so it goes through the coercion check to that slot with
        `commit`, which decides the list where the select's own coercion
        -- of the joined type -- no longer can. Returns the operand's type
        as the slot sees it."""
        result = self._check_compat(
            actual, expected, "select operand",
            getattr(source_expr, "loc", None), source_expr,
            coercion_ctx=coercion_ctx, commit=True)
        if isinstance(result, CompatError):
            raise SemanticError(result.message, result.loc)
        return self.pend.lists_as_known(actual)

    def _written_arg_entries(self, actual: TpyType, expected: TpyType,
                             ) -> 'list[tuple[object, ...]] | None':
        """The entries of a dict or set literal written as a call argument
        and scored open against several candidates
        (`ExpressionAnalyzer._await_winner`: no local, no cells, its
        numbers still the literals written), meeting a container of its
        own kind `expected`; None for anything else."""
        if not (isinstance(actual, PendingDictType) and is_dict(expected)
                or isinstance(actual, PendingSetType) and is_set(expected)):
            return None
        info = self.ctx.container_record(actual.literal_id)
        if (info is None or info.variable_name is not None
                or info.elem_cells is not None):
            return None
        return literal_entries(info.expr, actual) or None

    def _literal_values_fit(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None,
    ) -> 'CompatError | None':
        """The number literals of a list literal that still adapts (its
        element is a literal) against the element of the typed container
        it meets: the refusal of the first that does not fit. The literal's
        own element type keeps one value only, and a view (`Iterable[T]`)
        gives the literal no element hint, so nothing else checks them."""
        pending = pending_list_of(actual)
        elem = pending.element_type
        if not isinstance(elem, (IntLiteralType, FloatLiteralType)):
            return None
        info = self.ctx.list_literal(pending.literal_id)
        values = getattr(info.expr, "elements", None) if info else None
        if not values:
            return None
        container, _ = self.deduction.elem_container(expected, elem)
        if container is None:
            return None
        want = self.pend.leaf_want(container, (ELEM,))
        if value_family(want) is None:
            return None
        value = self.pend.first_unfit(values, want, literals_only=True)
        if value is None:
            return None
        # The range refusal, in the words of the scalar check.
        return self._check_compat(
            strip_int(self.ctx.get_expr_type(value)), want, context,
            getattr(value, "loc", None) or loc, value)

    def _check_polymorphic_slicing(
        self,
        actual: TpyType,
        expected: TpyType,
        source_expr: TpyExpr | None,
        context: str,
        loc: SourceLocation | None,
    ) -> None:
        """Reject the Phase-20 slicing shape: a borrow of a polymorphic class
        being stored into an owned slot of the same (or strict-subclass-of)
        type. Without this rejection the dynamic type is silently dropped
        when the value is copied into the slot. Recommended fix points at
        `Box[Throwable]` (or `Box[Polymorphic]` for non-exception bases).

        Allowed shapes (no slicing risk):
          - fresh-rvalue constructor call of the exact destination type
          - `None` assigned to an Optional slot
          - any non-polymorphic destination
        """
        if source_expr is None:
            return
        # Own[T] source carries move semantics -- the rvalue's dynamic type
        # transfers fully to the destination. Skip the borrow-shape check.
        if isinstance(actual, OwnType):
            return
        # Unwrap to find the owned slot's inner concrete type, if any.
        target_inner = expected
        if isinstance(target_inner, OwnType):
            target_inner = target_inner.wrapped
        if isinstance(target_inner, OptionalType):
            target_inner = target_inner.inner
        if not isinstance(target_inner, NominalType):
            return
        if not is_polymorphic_class_type(target_inner, self.ctx.registry):
            return
        # Fresh-rvalue construction (TpyCall with a class-name func). The
        # rvalue's dynamic type matches its static type at the construction
        # site, so codegen's Phase-18/19 subclass-into-Optional machinery
        # can materialize the temp at the concrete class with no slicing.
        # Covers both exact-type construction and concrete-subclass
        # construction of a polymorphic root.
        if isinstance(source_expr, TpyCall) and isinstance(source_expr.func, TpyName):
            if self.ctx.registry.get_record(source_expr.func.name) is not None:
                return
        # None into Optional -- trivially fine.
        if isinstance(source_expr, TpyNoneLiteral):
            return
        # Source is a borrow-shape: name, attribute, method call, function
        # call result. The dynamic type could differ from the static type
        # (caught exception, field of a base-class-typed slot, ...).
        is_borrow_shape = isinstance(
            source_expr,
            (TpyName, TpyFieldAccess, TpyMethodCall, TpyCall),
        )
        if not is_borrow_shape:
            return
        # The actual type must itself be a polymorphic class (or Optional[Poly])
        # for slicing to be possible. Strip qualifiers + Optional + Ref.
        src_inner = actual
        for _ in range(4):
            if isinstance(src_inner, RefType):
                src_inner = src_inner.wrapped
            elif isinstance(src_inner, OwnType):
                src_inner = src_inner.wrapped
            elif isinstance(src_inner, ReadonlyType):
                src_inner = src_inner.wrapped
            elif isinstance(src_inner, OptionalType):
                src_inner = src_inner.inner
            else:
                break
        if not isinstance(src_inner, NominalType):
            return
        if not is_polymorphic_class_type(src_inner, self.ctx.registry):
            return
        # Fresh-ctor local: a non-reassigned local whose sole binding is a
        # constructor call of its exact static type has a provably-known dynamic
        # type (== its static type), so moving it into the owned poly slot cannot
        # slice -- same guarantee as the fresh-rvalue ctor above, one binding hop
        # removed. (Field mutation between construction and store is fine -- it is
        # not a rebind, so the prescan leaves the name eligible.)
        if (isinstance(source_expr, TpyName)
                and self.ctx.func is not None
                and source_expr.name in self.ctx.func.current_fresh_ctor_locals):
            return
        # A return exception has neither `clone()` nor a `Box[Throwable]` form,
        # so the exception remedy below would point at code that does not
        # compile; it still has no owned storage form of its own.
        src_info = self.ctx.registry.get_record_for_type(src_inner)
        if src_info is not None and src_info.is_return_exception:
            raise SemanticError(
                f"cannot store the return-only exception '{src_inner.name}' as "
                f"an owned value in {context}: keeping a caught return "
                f"exception is not supported yet; copy the fields you need "
                f"out of it instead",
                loc,
            )
        # Tailor the remediation: exception roots have the idiomatic
        # `Box[Throwable]` shared-base form; other polymorphic roots point at
        # their own `Box[Root]`.
        if is_exception_type(target_inner.name, self.ctx.registry):
            hint = (f"Use `Box[Throwable]` (or `Box[{target_inner.name}]`) for "
                    f"owned polymorphic storage: `slot = Box(exc.clone())`.")
        else:
            hint = (f"Use `Box[{target_inner.name}]` for owned polymorphic "
                    f"storage: construct into the `Box` directly, or "
                    f"`Box(value.clone())`.")
        raise SemanticError(
            f"cannot store '{src_inner.name}' borrow as owned "
            f"'{target_inner.name}' in {context} -- the dynamic type may "
            f"be a subclass and would be lost (slicing). " + hint,
            loc,
        )

    @staticmethod
    def _sink_owns_its_value(
        expected: TpyType, coercion_ctx: 'CoercionContext | None',
        target_is_storage_form: bool,
    ) -> bool:
        """Whether the slot OWNS what it is handed, rather than borrowing it.

        `target_is_storage_form` is the caller's own statement that the
        destination is a field or a container element, which is the storage
        half of the question -- so it is read here rather than re-derived from
        the context, and a literal element that fails to set it is a hole in
        the owning verdict as much as in the address-take one. `Own[...]` is
        the second owning face. Of the remaining KNOWN contexts only a plain
        argument borrows; an unknown context is treated as borrowing so a sink
        this cannot see stays admitted rather than wrongly rejected.
        """
        if target_is_storage_form or isinstance(expected, OwnType):
            return True
        return (coercion_ctx is not None
                and coercion_ctx is not CoercionContext.ARG)

    def _conversion_hint(
        self, actual: TpyType, expected: TpyType,
        coercion_ctx: 'CoercionContext | None', sink_owns: bool,
    ) -> str:
        """`-- write 'T(...)' around it`, for the one mismatch where a copy is
        exactly what is missing.

        A borrow-only row means the value BINDS at a borrowing destination;
        arriving at an owning one, all it lacks is the copy, and spelling the
        destination type around it is the copy. Every other mismatch is left
        alone: a pair with no such row needs a conversion, a parse or a
        different value, none of which this sentence would describe.
        The spelling is the destination type's own display name, so no pair of
        types is named here.
        """
        if borrow_only_veto(actual, expected, coercion_ctx, sink_owns) is None:
            return ""
        target = unwrap_ref_type(unwrap_own(unwrap_readonly(expected)))
        return f" -- write '{target}(...)' around it"

    def check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: CoercionContext | None = None,
        target_is_storage_form: bool = False,
    ) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Raises SemanticError on incompatible types.
        Returns a Coercion if a conversion should be applied at codegen time,
        or None if compatible with no coercion needed.
        """
        # Phase 20 Stage 5a: reject the slicing shape -- storing a borrow
        # of a polymorphic class into an owned slot of the same type would
        # silently drop the dynamic type. Fires before _check_compat so the
        # user sees a targeted "use Box[Throwable]" diagnostic rather than
        # a generic type mismatch.
        self._check_polymorphic_slicing(actual, expected, source_expr, context, loc)
        pending = self._pending_num_ends(actual, expected, source_expr, context)
        if pending is not None:
            if pending:
                self.pend.check(strip_int(actual), strip_int(expected), context,
                                coercion_ctx, source_expr)
            return None
        actual = self.pend.current(actual)
        # A dict or set slot of a call resolved against a container whose
        # leaves have settled names them too.
        expected = self.pend.current(expected)
        # A check that produces the coercion is where a list literal's
        # element is decided by the typed container it meets.
        result = self._check_compat(actual, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, commit=True)
        if isinstance(result, CompatError):
            # A literal-seeded local takes its type from what is stored in
            # it, never from a use: say what declares the type the use wants.
            hint = (self.pend.annotation_hint(source_expr.name, expected)
                    if isinstance(source_expr, TpyName)
                    else self.pend.elem_annotation_hint(source_expr, expected,
                                                        coercion_ctx))
            raise SemanticError(result.message + hint, result.loc)
        return result

    def _pending_num_ends(self, actual: TpyType, expected: TpyType,
                          source_expr: TpyExpr | None,
                          context: str) -> bool | None:
        """How a check or conversion with a pending number at one end goes:
        None when neither end is pending (the ordinary way); True when it
        waits for the function to settle (a numeric slot); False when there
        is nothing to convert (a literal stored into a pending local). A
        pending value at any other slot is settled here, and the check goes
        the ordinary way."""
        # A leaf whose cells have all settled converts as its type does: a
        # body that is not the cells' own (a nested function writing a
        # captured container) has nothing that would resolve it later.
        a = strip_int(self.pend.current(actual))
        e = strip_int(self.pend.current(expected))
        if not (isinstance(a, PendingNumType) or isinstance(e, PendingNumType)):
            if isinstance(e, TupleType) and value_leaves(e):
                # A tuple read or stored with pending members: its members
                # convert as a pending number does, once they settle.
                return True
            if isinstance(a, TupleType) and value_leaves(a):
                if isinstance(e, TupleType) and all(
                        is_numeric_slot(m) or not value_leaves(n)
                        for m, n in zip(e.element_types, a.element_types)):
                    return True
                self.pend.force_value(source_expr, a, what=context)
            return None
        if isinstance(e, PendingNumType):
            return not isinstance(a, (IntLiteralType, FloatLiteralType))
        if is_numeric_slot(e):
            return True
        self.pend.force(source_expr, a, what=context)
        return None

    def _value_frame_traits(self, source_expr: TpyExpr | None) -> tuple[bool, bool] | None:
        """(is_send, is_sync) of the concrete captured-state frame behind a
        value expression, when sema knows it: a lambda's capture list, or a
        function reference's FunctionInfo (free function / nested def).
        None when no frame fact is available (conservative caller default).
        """
        if isinstance(source_expr, TpyLambda):
            frame = source_expr.frame_type
            if frame is not None:
                return (frame.is_send(), frame.is_sync())
            return None
        if isinstance(source_expr, TpyName):
            fi = getattr(source_expr, "function_ref_info", None)
            if fi is not None:
                return frame_traits_of_function(fi)
        return None

    def _value_frame(self, source_expr: TpyExpr | None) -> FrameType | None:
        """The concrete FrameType behind a value expression, for diagnostics
        that name the offending captured slot (vs _value_frame_traits' bool).
        Own-frame only -- sub-frame causes are absent, so the caller treats a
        childless chain as 'no per-value detail' and falls back to the static
        type. None when no frame fact is available."""
        if isinstance(source_expr, TpyLambda):
            return source_expr.frame_type
        if isinstance(source_expr, TpyName):
            fi = getattr(source_expr, "function_ref_info", None)
            if fi is not None:
                return frame_type_of_function(fi)
        return None

    def _resolve_recursive_refs(self, typ: TpyType) -> TpyType:
        """Resolve AliasRef self-references to recursive union aliases.

        Handles both bare AliasRef("Tree") and AliasRef nested inside
        containers (e.g. list[AliasRef("Tree")] -> list[UnionType(...)]).
        Treats recursive union types as opaque (does not recurse into their
        members) to prevent infinite expansion of self-referencing placeholders.
        """
        if isinstance(typ, AliasRef):
            # Module-aware: resolves a cross-module recursive alias the caller
            # didn't import (so a container arg matches the alias's
            # list[Alias] / dict[_, Alias] members). See resolve_alias_ref.
            alias = self.ctx.registry.resolve_alias_ref(typ)
            if alias is not None:
                return alias
        # Generic recursive alias instances are opaque, like the non-generic
        # UnionType wrapper below -- their alternatives live behind the
        # wrapper and must not be expanded here.
        if isinstance(typ, RecursiveAliasInstanceType):
            return typ
        if not self.ctx.recursive_union_names:
            return typ
        # Don't recurse into recursive union aliases -- their AliasRef
        # placeholders are structural and must not be expanded.
        if isinstance(typ, UnionType) and typ.needs_wrapper():
            return typ
        inner = typ.inner_types()
        if not inner:
            return typ
        new_inner = tuple(self._resolve_recursive_refs(t) for t in inner)
        if all(new is old for new, old in zip(new_inner, inner)):
            return typ
        return typ.with_inner_types(new_inner)

    def _return_exception_as_throwable(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None,
    ) -> CompatError | None:
        """A ReturnException value offered where a thrown-exception type is
        expected. It lists `Exception` among its Python bases, so CPython
        accepts this; in TPy it is a plain value outside the Throwable
        hierarchy, and the generic mismatch text would not say why."""
        source = unwrap_readonly(unwrap_own(actual))
        target = unwrap_readonly(unwrap_own(expected))
        if isinstance(target, OptionalType):
            target = unwrap_readonly(target.inner)
        if not (isinstance(source, NominalType) and isinstance(target, NominalType)):
            return None
        source_info = self.ctx.registry.get_record_for_type(source)
        if source_info is None or not source_info.is_return_exception:
            return None
        if target.qualified_name() != qnames.THROWABLE:
            target_info = self.ctx.registry.get_record_for_type(target)
            if (target_info is None or target_info.is_return_exception
                    or not (target_info.inherits_base_exception
                            or target_info.qualified_name() == qnames.BASE_EXCEPTION)):
                return None
        return CompatError(
            f"Type mismatch in {context}: '{source}' is a return-only exception "
            f"(ReturnException) and cannot be used as '{target}'; it is a plain "
            f"value that is never thrown. Build a regular exception from it, "
            f"e.g. RuntimeError(str(e))", loc)

    def _check_compat(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: CoercionContext | None = None,
        target_is_storage_form: bool = False,
        sink_owns: bool | None = None,
        commit: bool = False,
    ) -> CompatResult:
        """Core type compatibility check.

        Returns Coercion or None on success, CompatError on failure.

        commit: the check produces the coercion of this very value into
        this very slot, so it decides the element of a list literal that
        meets a typed container (`_list_at_container`). Handed down only
        where the recursion peels a wrapper off the same value and slot,
        pairs a tuple's elements, or re-checks the union member that
        admitted the value; a probe never passes it.

        target_is_storage_form: True when the destination is a field or
        container element (value-storage form). Used to suppress address-
        take mutation marking that only applies to borrow-form destinations
        (params/locals/returns); storage-form assignments copy the value.

        sink_owns: whether this destination owns what it is handed. Decided
        once at the outermost level, where an `Own[...]` is still visible,
        and handed down as the recursion peels wrappers; a borrow-only
        coercion row is not admitted at an owning destination. Deriving it
        again at the leaf would read a stripped `Own[...]` as a borrowing
        sink. None means "not decided yet" -- derive it here.
        """
        # Resolve NominalType self-references from recursive union members.
        # e.g. NominalType("Tree") -> UnionType, and also inside containers:
        # list[NominalType("Tree")] -> list[UnionType(...)].
        # Uses seen-set guard to prevent infinite recursion.
        actual = self._resolve_recursive_refs(actual)
        expected = self._resolve_recursive_refs(expected)

        if sink_owns is None:
            sink_owns = self._sink_owns_its_value(
                expected, coercion_ctx, target_is_storage_form)

        if (pending_list_of(actual) is not None
                or self.pend.cell_container(actual) is not None):
            met = self._list_at_container(actual, expected, context, loc,
                                          source_expr, coercion_ctx, commit)
            if isinstance(met, CompatError):
                return met
            if met is not None:
                actual = met

        # A NAME feeding a slot that owns it must reach the Own[T] coercion
        # path even when the types are already equal: that path is where the
        # source's consumption is DECIDED -- the copy warning at a non-last-use
        # and, through `is_auto_move_use`, the hidden-borrow demotion that
        # retracts a last-use mark while a call-result borrow of the local is
        # live. Skipping it hands codegen an unretracted mark and it moves the
        # storage out from under the borrow.
        # A tuple keeps its per-element `Own` markers when the local is bound
        # from an owning CALL (`t = mk(i)`), so its type collapses equal to the
        # owning slot's and the equality shortcut would skip the whole decision;
        # a literal init drops the markers and only reaches the path by
        # mismatch. Tuples are the only equal-shape carrier: an `Own` under a
        # local's Optional/container is rejected as redundant at declaration.
        if actual == expected:
            owning_name_source = isinstance(source_expr, TpyName) and (
                isinstance(actual, OwnType)
                or (isinstance(actual, TupleType) and actual.has_nested_own_element()))
            if not owning_name_source:
                return None
        ret_exc_error = self._return_exception_as_throwable(
            actual, expected, context, loc)
        if ret_exc_error is not None:
            return ret_exc_error
        # Send[T] / Sync[T] marker wrappers. The wrapper has no C++
        # representation; the conversion into a marker-typed slot is the
        # assertion site (markers persist post-resolution only around
        # erased types, where the trait is a per-value property).
        if isinstance(expected, (SendType, SyncType)):
            trait = "Send" if isinstance(expected, SendType) else "Sync"
            holds = actual.is_send() if isinstance(expected, SendType) \
                else actual.is_sync()
            if not holds:
                # Erased callables answer non-Send at the type level; the
                # concrete value may still qualify via its captured-state
                # frame (lambda capture list / function-ref FunctionInfo).
                frame_traits = self._value_frame_traits(source_expr)
                if frame_traits is not None:
                    holds = frame_traits[0] if isinstance(expected, SendType) \
                        else frame_traits[1]
            if not holds:
                is_send = isinstance(expected, SendType)
                # Prefer the value's concrete frame chain (names the offending
                # capture) over the static erased-callable leaf; fall back when
                # the frame has no own-slot cause to attribute.
                chain = None
                frame = self._value_frame(source_expr)
                if frame is not None:
                    fc = why_not_frame(frame, str(actual), is_send)
                    if fc is not None and fc.children:
                        chain = fc
                if chain is None:
                    chain = why_not_send(actual) if is_send else why_not_sync(actual)
                detail = f"\n{render_chain(chain, is_send)}" if chain is not None else ""
                return CompatError(
                    f"'{actual}' is not {trait} -- cannot use it where "
                    f"'{expected}' is expected in {context}{detail}", loc)
            return self._check_compat(
                unwrap_send_sync(actual), expected.wrapped, context, loc,
                source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns,
                commit)
        if isinstance(actual, (SendType, SyncType)):
            # Marker-typed value into an unmarked slot: the marker only adds
            # a guarantee, so it converts freely to the bare type.
            return self._check_compat(
                actual.wrapped, expected, context, loc, source_expr,
                is_return, coercion_ctx, target_is_storage_form, sink_owns,
                commit)
        # CallableType (Fn and Callable): inner param/return types may carry
        # Own/Ref qualifiers from FI that don't affect callable contract compatibility.
        if (is_callable_type(actual)
                and is_callable_type(expected)
                and len(actual.param_types) == len(expected.param_types)):
            stripped_actual = type(actual)(
                tuple(unwrap_ref_type(unwrap_own(p)) for p in actual.param_types),
                unwrap_ref_type(unwrap_own(actual.return_type)))
            stripped_expected = type(expected)(
                tuple(unwrap_ref_type(unwrap_own(p)) for p in expected.param_types),
                unwrap_ref_type(unwrap_own(expected.return_type)))
            if stripped_actual == stripped_expected:
                return None
        # None literal (NoneType) is compatible with None annotation (VoidType)
        if isinstance(actual, NoneType) and isinstance(expected, VoidType):
            return None

        # Pending generic instance: try to resolve from the expected type
        if isinstance(actual, PendingGenericInstanceType):
            resolved = self.methods.try_resolve_pending_from_expected_type(
                actual, expected, loc, commit=commit)
            if resolved is not None:
                if commit and source_expr is not None:
                    self.ctx.set_expr_type(source_expr, resolved)
                return self._check_compat(
                    resolved, expected, context, loc, source_expr,
                    is_return, coercion_ctx, target_is_storage_form, sink_owns)
            return CompatError(
                f"Type mismatch in {context}: '{actual.record_name}' has unresolved type "
                f"arguments; call a constraining method first or add explicit type arguments",
                loc,
            )

        # Ref[T] in expected: strip Ref and check inner type.
        # Ref is a codegen annotation (reference semantics); for type
        # compatibility, T is compatible with Ref[T].
        if isinstance(expected, RefType):
            return self._check_compat(
                unwrap_ref_type(actual), expected.wrapped, context, loc,
                source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns,
                commit)
        # Strip Ref from actual too (Ref[T] is compatible with T)
        if isinstance(actual, RefType):
            return self._check_compat(
                actual.wrapped, expected, context, loc,
                source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns,
                commit)

        # readonly[T] -> readonly[T]: unwrap and check inner types
        # T -> readonly[T]: always OK (adding const is safe)
        if isinstance(expected, ReadonlyType):
            actual_inner = unwrap_readonly(actual)
            return self._check_compat(
                actual_inner, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form,
                sink_owns, commit
            )

        # readonly[T] -> T: error for non-value types (stripping const is unsafe)
        # Exceptions: value types (copies), readonly protocols (Sized, Sequence),
        # return values (C++ const method handles safety via const propagation)
        # Note: mutable Span is NOT excepted even for returns -- you cannot
        # construct a mutable span from a const container.
        if isinstance(actual, ReadonlyType) and not isinstance(expected, ReadonlyType):
            is_mutable_span = is_span(expected) and not is_readonly_span(expected)
            if (not expected.is_value_type() or is_mutable_span) and (not is_return or is_mutable_span):
                allow = False
                if is_protocol_type(expected) and self.protocols:
                    proto_info = protocol_info_of(expected)
                    if proto_info and self.protocols.is_all_readonly(proto_info):
                        allow = True
                if not allow:
                    return CompatError(
                        f"Cannot return readonly[{actual.wrapped}] as mutable {expected}; "
                        f"use Span[readonly[T]] or annotate return type with auto_readonly[T]"
                        if is_return else
                        f"Cannot pass readonly[{actual.wrapped}] as mutable {expected} in {context}",
                        loc,
                    )
            return self._check_compat(
                actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form,
                sink_owns, commit
            )

        # None -> Optional[T] / Ptr[T] / Ptr[readonly[T]]: always compatible
        if isinstance(actual, NoneType) and isinstance(expected, (OptionalType, PtrType)):
            return None

        # T -> Any: any copyable type stores into Any. Move-only sources
        # (Own[T] / @nocopy records / containers thereof) are rejected --
        # v1 supports copyable contents only. The INTO_ANY coercion wraps
        # the source as `tpy::Any{std::any{value}, &any_ops_for<T>}` and
        # upgrades view types (StrView, BytesView) to their owning form.
        if isinstance(expected, AnyType) and not isinstance(actual, AnyType):
            actual_for_check = unwrap_ref_type(actual)
            if self.ctx.is_type_nocopy(actual_for_check):
                return CompatError(
                    f"cannot store move-only type '{actual}' in Any "
                    f"(v1 supports copyable contents only)",
                    loc,
                )
            # Storing a reference type (record/list/dict/set) into Any
            # silently copies (Principle #1: Any owns its contents).
            # That contradicts TPy's reference-type baseline -- elsewhere a
            # copy of a record/container requires explicit copy(). Suppressed
            # for rvalue sources (no other handle), copy()-wrapped sources,
            # and last-use auto-move (matches the dict.update convention).
            inner = unwrap_own(unwrap_readonly(actual_for_check))
            if (source_expr is not None
                    and self.is_lvalue(source_expr)
                    and not self.is_copy_call(source_expr)
                    and not self._is_auto_moved(source_expr)
                    and self._warns_copy_into_any(inner)):
                self.ctx.warning(
                    f"copies {inner} into Any; use copy() to make this explicit",
                    source_expr,
                )
            return INTO_ANY

        # Any -> T: runtime-checked auto-coerce. Target T must be a concrete
        # type -- Union / Optional / generic-type-param / protocol targets
        # are too ambiguous (which member to extract? what concrete T to
        # any_cast against?). Users narrow first via isinstance or call
        # typing.cast(T, x) explicitly. Codegen emits any_cast_or_panic<T_cpp>;
        # typeid mismatches panic at runtime with the documented message.
        # Protocol targets fall through to the structural-conformance path
        # (Any satisfies Hashable / Equatable / Stringable / Representable
        # at the type-system level; runtime ops may panic if the contained
        # T lacks the capability). Own[Any] / readonly[Any] targets also
        # fall through (Any -> Any is a no-op, not an extraction).
        if isinstance(actual, AnyType):
            expected_inner = expected
            if isinstance(expected_inner, OwnType):
                expected_inner = expected_inner.wrapped
            expected_inner = unwrap_readonly(expected_inner)
            if isinstance(expected_inner, AnyType):
                return None
            if isinstance(expected_inner, (UnionType, OptionalType)):
                return CompatError(
                    f"cannot auto-coerce Any to {expected} -- "
                    f"narrow first via isinstance(x, T) or typing.cast(T, x)",
                    loc,
                )
            if isinstance(expected_inner, TypeParamRef):
                return CompatError(
                    f"cannot auto-coerce Any to generic type parameter "
                    f"'{expected_inner.name}' -- typing.cast(ConcreteT, x) first",
                    loc,
                )
            if not is_protocol_type(expected_inner):
                return FROM_ANY

        # Union[A, B] -> Union[A, B, C]: each actual member must match some expected member
        if isinstance(actual, UnionType) and isinstance(expected, UnionType):
            for member in actual.members:
                result = self._check_compat(member, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns)
                if isinstance(result, CompatError):
                    return result
            return None

        # T -> Union[T, ...] (and T -> a generic recursive alias wrapper, whose
        # alternatives stand in for the union members): actual must match at
        # least one member. Iterate twice so a category-crossing widening (e.g.
        # int -> float) never wins when an in-category member exists. Without
        # this, an int going into `int | float` would silently coerce to float
        # when float happens to be earlier in the canonical member order.
        union_members: 'tuple[TpyType, ...] | None' = None
        if isinstance(expected, UnionType):
            union_members = expected.members
        elif isinstance(expected, RecursiveAliasInstanceType):
            union_members = expected.alternatives()
        if union_members is not None:
            actual_unwrapped = unwrap_own(actual)
            # Own[Union] -> a compatible Union (e.g. `d: JsonValue =
            # json.loads(s)`): the per-member loop below can't see whole-union
            # compatibility -- no single member equals the whole union -- so
            # strip Own and let the Union->Union path match member-wise. The
            # ownership transfers with the move. Restricted to union sources so
            # `Own[Dog] -> Dog | Cat` keeps its pointer-variant member coercion.
            if (isinstance(actual, OwnType)
                    and isinstance(actual_unwrapped, (UnionType, RecursiveAliasInstanceType))):
                return self._check_compat(
                    actual_unwrapped, expected, context, loc, source_expr,
                    is_return, coercion_ctx, target_is_storage_form, sink_owns,
                    commit)
            for member in _union_member_order(actual_unwrapped, union_members):
                result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns)
                if not isinstance(result, CompatError):
                    if commit:
                        # The member that admits the value is the slot it
                        # goes to: what the check writes about the value is
                        # written against that member, not against one
                        # tried and refused.
                        return self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, commit)
                    return result
            # A concrete container variable whose elements would each fit a union
            # member is NOT implicitly converted: an element-wise rebuild into the
            # wrapper representation is a hidden O(n) deep copy, which TPy declines
            # to insert silently (an aliasing reference type would also diverge
            # from CPython). Point at the explicit alternatives rather than the
            # bare type-mismatch. (Container literals are unaffected -- they
            # materialize into the wrapper directly.)
            if self.deep_convertible_to_union(actual_unwrapped, expected):
                return CompatError(
                    f"Type mismatch in {context}: a concrete container ({self.diag_type(actual)}) "
                    f"is not implicitly converted into the recursive-union type "
                    f"{expected} (it would be a hidden element-wise deep copy). "
                    f"Build it as the alias directly (`x: {expected} = {{...}}`) or "
                    f"pass a container literal.", loc)
            e, a = self._pair(expected, actual)
            return CompatError(f"Type mismatch in {context}: expected {e}, got {a}", loc)

        # T -> Optional[T]: implicit wrapping
        if isinstance(expected, OptionalType):
            if isinstance(actual, OwnType):
                actual_inner = actual.wrapped
            else:
                actual_inner = actual
            # Own[Optional[T]] -> Optional[T]: exact match after unwrap
            if actual_inner == expected:
                return None
            # Optional[Own[T]] -> Optional[T]: unwrap Own inside Optional
            if isinstance(actual_inner, OptionalType) and isinstance(actual_inner.inner, OwnType):
                actual_inner = OptionalType(actual_inner.inner.wrapped)
                if actual_inner == expected:
                    return None
            # For pointer-repr Optional[T], codegen takes &(source) when source is a
            # non-value record (not already Optional/Ptr). Mark source params as needing
            # T& so &(param) stays valid (not const T&). Skip when the destination
            # is in storage form (field / container element) -- the Optional there
            # lowers to std::optional<T> (value-storage) and the assignment is a
            # copy; no address-take happens, so the source's const-ness is fine.
            if (expected.uses_pointer_repr()
                    and source_expr is not None
                    and not actual_inner.is_value_type()
                    and not isinstance(actual_inner, (OptionalType, PtrType, NoneType))
                    and not target_is_storage_form):
                self._mark_addr_taken(source_expr)
            # Ptr[T] -> T | None (pointer-repr): byte-identical T* at C++ level.
            # Per-context idiom: Ptr[T] for storage, T | None for nullable returns.
            if (isinstance(actual_inner, PtrType)
                    and expected.uses_pointer_repr()
                    and _inner_compat_with_ro_widening(actual_inner.pointee, expected.inner)):
                return None
            # Optional[A] -> Optional[B] via a whole-Optional coercion rule
            # (e.g. str <-> StrView at arg position, where both lower to
            # `std::optional<std::string_view>`). Try before stripping to the
            # inner, since the inner check can't see that the wrappers share
            # a C++ representation.
            if isinstance(actual_inner, OptionalType):
                whole = resolve_coercion(actual_inner, expected,
                                         coercion_ctx, sink_owns)
                if whole is not None:
                    return whole
            # Optional[T] -> Optional[Own[T]]: ownership transfer wrapped in
            # Optional (e.g. returning an owned `Foo | None` local as
            # `Own[Foo] | None`). Compare the INNERS so the bare T -> Own[T]
            # coercion applies its ownership semantics (move at last use,
            # copy-warn / @nocopy-reject otherwise) to the optional source.
            # (`Own[Foo | None]` -- OwnType outer -- is handled by the OwnType
            # branch below, which matches the whole Optional after unwrap.)
            if (isinstance(actual_inner, OptionalType)
                    and isinstance(expected.inner, OwnType)
                    and not isinstance(actual_inner.inner, OwnType)):
                inner_result = self._check_compat(
                    actual_inner.inner, expected.inner, context, loc,
                    source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns)
                if inner_result is None:
                    return None
            result = self._check_compat(actual_inner, expected.inner, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, commit)
            # Rewrap inner-mismatch errors with the declared Optional types so
            # the diagnostic reads `expected str | None, got StrView | None`
            # rather than the truncated `expected str, got StrView | None`.
            if isinstance(result, CompatError) and isinstance(actual, OptionalType):
                e, a = self._pair(expected, actual)
                return CompatError(
                    f"Type mismatch in {context}: expected {e}, got {a}", loc)
            return result

        # Optional[T] -> Optional[T] already handled by == check above
        # Optional[T] -> T: error (cannot implicitly unwrap)

        # T | None (pointer-repr) -> Ptr[T]: byte-identical T* at C++ level.
        # Per-context idiom: T | None for nullable returns, Ptr[T] for storage.
        # Storage-form Optional sources (field access, container element) need
        # an `optional_to_ptr` lift at codegen, mirroring the existing
        # storage-form-to-borrow-form lift for OptionalType destinations.
        if isinstance(expected, PtrType):
            actual_for_opt = unwrap_own(actual)
            if (isinstance(actual_for_opt, OptionalType)
                    and actual_for_opt.uses_pointer_repr()
                    and _inner_compat_with_ro_widening(actual_for_opt.inner, expected.pointee)):
                return None

        # NominalType with Own[T] in type args signals copy semantics -- applies to both
        # protocols (Iterable[Own[T]]) and concrete containers (dict[K, Own[V]]).
        # Strip Own for conformance/equality check; warn when elements are implicitly copied.
        if (isinstance(expected, NominalType)
                and expected.inner_types()
                and any(isinstance(a, OwnType) for a in expected.inner_types())):
            stripped_expected = strip_own_type_args(expected)
            # Own[T] actual means caller acknowledged ownership transfer -- no warning.
            actual_inner = unwrap_own(actual)
            # Symmetric with the expected-side strip: an actual that itself
            # carries Own in its type args (e.g. Iterator[Own[T]]) already
            # yields owned elements, so strip those too -- otherwise it would be
            # compared element-wise (Own[T]) against the bare-T stripped
            # expected and wrongly fail. Provides-Own means no implicit copy.
            actual_provides_own = (
                isinstance(actual_inner, NominalType)
                and bool(actual_inner.inner_types())
                and any(isinstance(a, OwnType) for a in actual_inner.inner_types())
            )
            if actual_provides_own:
                actual_inner = strip_own_type_args(actual_inner)
            if is_protocol_type(expected):
                structurally_ok = bool(
                    self.protocols and self.protocols.type_conforms_to_protocol(actual_inner, stripped_expected)
                )
            else:
                # Concrete type: exact match after Own stripping
                structurally_ok = (actual_inner == stripped_expected)
            if structurally_ok:
                # Own[T] from explicit return/param means caller acknowledged
                # ownership -- skip warning.  Own[T] from name lookup (owned
                # local) is implicit and still needs the warning.
                explicit_own = (isinstance(actual, OwnType)
                                and not isinstance(source_expr, TpyName))
                if (source_expr is not None
                        and not explicit_own
                        and not actual_provides_own
                        and not self.is_copy_call(source_expr)):
                    is_lvalue_src = self.is_lvalue(source_expr)
                    # Suppress at last use: no observable semantic divergence from
                    # CPython when the source is dead after this point (Framing A).
                    is_auto_moved = self._is_auto_moved(source_expr)
                    # A dead HANDLE moves only itself: the elements it hands
                    # out still live in the storage it lends from.
                    lent_value = (self._dead_handle_value(source_expr)
                                  if is_auto_moved else None)
                    prov_src = source_expr
                    if lent_value is not None:
                        prov_src, is_lvalue_src = lent_value, False
                    if not is_auto_moved or lent_value is not None:
                        for inner_t in expected.inner_types():
                            if not isinstance(inner_t, OwnType):
                                continue
                            elem_type = inner_t.wrapped
                            if not own_copy.contains_reference_type(elem_type):
                                continue
                            display_type = unwrap_ref_type(elem_type)
                            # Lvalue source (container): suggest copy_iter or copy.
                            # Rvalue (iterator): only copy_iter.
                            if is_lvalue_src:
                                hint = "use copy_iter() to make this explicit (or copy() to copy the entire container)"
                            else:
                                hint = "use copy_iter() to make this explicit"
                            # Rvalue iterators that yield Ref elements (e.g.
                            # map(identity, pts)) copy on materialization.
                            # RefType in the Own-wrapped element is the proof.
                            if not (is_lvalue_src or _contains_semantic_ref(elem_type)):
                                # A combinator, view or generator lends its
                                # sources' elements with no Ref marker: the
                                # proof is their provenance.
                                if iteration_lend_pending(self.ctx, prov_src):
                                    # A generic payload's hedge must be filed
                                    # before the copy verdicts discharge, so it
                                    # assumes the pending callee lends.
                                    if not own_copy.type_has_type_param(display_type):
                                        self.pending_iter_copy_checks.append(
                                            PendingIterCopyCheck(
                                                prov_src, elem_type, display_type,
                                                hint, self.ctx._resolve_loc(source_expr)))
                                        continue
                                elif not iteration_copies_lent_reference(
                                        self.ctx, prov_src, elem_type):
                                    continue
                            if self.ctx.defer_own_copy_verdict(
                                    display_type, display_type,
                                    "owned storage", source_expr,
                                    kind=own_copy.KIND_ELEMENTS, hint=hint):
                                continue
                            self.ctx.warning(
                                f"copies {self.diag_type(display_type)} "
                                f"elements; {hint}",
                                source_expr,
                            )
                return None
            if is_protocol_type(expected):
                return CompatError(
                    f"Type {actual_inner} does not conform to protocol {stripped_expected} in {context}",
                    source_expr.loc if source_expr is not None else loc
                )
            # Concrete type mismatch: fall through to generic error handling

        # Protocol matching (structural subtyping)
        if is_protocol_type(expected):
            # Own[T] conforms to any protocol that T conforms to
            check_actual = actual.wrapped if isinstance(actual, OwnType) else actual
            if self.protocols and self.protocols.type_conforms_to_protocol(check_actual, expected):
                return None  # No coercion needed, structural match
            return CompatError(
                f"Type {check_actual} does not conform to protocol {expected} in {context}",
                loc
            )

        # Callable object -> Fn/Callable: record with a __call__ overload
        # satisfying the expected signature (first declared match wins).
        # Plain signature-to-signature matching, not resolve_overload --
        # that machinery scores call-site ARG types; here two signatures
        # are compared, with the same variance rule as Callable -> Fn.
        if isinstance(actual, NominalType) and is_callable_type(expected):
            record = self.ctx.registry.get_record_for_type(actual)
            if record:
                overloads = self.ctx.registry.get_method_overloads_with_parents(record, "__call__")
                if overloads:
                    for fi in overloads:
                        if (len(fi.params) == len(expected.param_types)
                                and self._callable_signature_satisfies(
                                    tuple(p.type for p in fi.params), fi.return_type,
                                    expected, loc)):
                            return None
                    candidates = "; ".join(
                        f"({', '.join(str(p.type) for p in fi.params)}) -> {fi.return_type}"
                        for fi in overloads)
                    return CompatError(
                        f"no '__call__' overload of '{actual}' matches {expected} "
                        f"in {context} (candidates: {candidates})",
                        loc,
                    )

        # Callable -> Fn: std::function satisfies template requires clauses in C++
        if isinstance(actual, CallableType) and is_fn_type(expected):
            if (len(actual.param_types) == len(expected.param_types)
                    and self._callable_signature_satisfies(
                        actual.param_types, actual.return_type, expected, loc)):
                return None

        # Inheritance: Child -> Parent (implicit value upcast, C++ handles slicing/ref binding)
        if (isinstance(actual, NominalType) and actual.is_user_record
                and isinstance(expected, NominalType) and expected.is_user_record):
            if self.ctx.registry.is_subclass_of(actual, expected):
                if coercion_ctx in (CoercionContext.ASSIGN,
                                    CoercionContext.INIT,
                                    CoercionContext.RETURN):
                    msg = (f"upcast narrows '{actual}' to '{expected}' -- "
                           f"only '{expected}' fields and methods will be accessible; "
                           f"keep the concrete type or make '{expected}' a @dynamic protocol")
                    if source_expr is not None:
                        self.ctx.warning(msg, source_expr)
                    else:
                        self.ctx.warning_from_loc(msg, loc)
                return None

        # Inheritance: Ptr[Child] -> Ptr[Parent] / Ptr[readonly[Parent]]
        # Ptr[readonly[Child]] -> Ptr[readonly[Parent]]
        # Covers both class-to-class inheritance and `@dynamic` protocol
        # implementation; C++ upcasts the pointer implicitly at the call
        # site via virtual inheritance.
        if isinstance(actual, PtrType) and isinstance(actual.inner_pointee, NominalType):
            if isinstance(expected, PtrType) and isinstance(expected.inner_pointee, NominalType):
                if _ptr_readonly_compatible(actual, expected):
                    if self._is_covariant_target(actual.inner_pointee, expected.inner_pointee):
                        return None

        # Ptr[U] -> Ptr[B] where U is a type param whose subtype bound reaches
        # B (`def f[U: B]`). Borrow-form pointer upcast only; the C++ upcast is
        # only valid when the concrete U is a real subtype of B, which the call
        # site enforces (see methods/calls bound validation).
        if (isinstance(actual, PtrType) and isinstance(expected, PtrType)
                and isinstance(actual.inner_pointee, TypeParamRef)
                and _ptr_readonly_compatible(actual, expected)
                and self._is_representational_subtype(
                    actual.inner_pointee, expected.inner_pointee)):
            return None

        # Covariant generic coercion: Box[Child] -> Box[Parent]
        # when Box extends Covariant[T] and Child conforms to @dynamic Parent.
        # C++ converting move ctor handles the actual conversion.
        if self.is_covariant_generic_upcast(actual, expected):
            return None

        # A concrete std container (list/dict/set) whose element type differs
        # from a recursive-union-alias element cannot be converted element-wise:
        # std::vector / tpy::ordered_map have no element-converting constructor
        # (unlike the single-element Box/Rc wrappers handled above, whose
        # template ctor does the conversion). Accepting it would emit an
        # ill-formed deep conversion (`ordered_map<string,int> -> JsonValue`),
        # so reject with a clean diagnostic. A literal takes the
        # PendingList/PendingDict branches below (materialized element-wise) and
        # is unaffected. Proper element-wise container->wrapper conversion is
        # tracked in BUGS.md.
        if ((is_list(actual) or is_dict(actual) or is_set(actual))
                and isinstance(expected, NominalType)
                and isinstance(actual, NominalType)
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and actual.type_args != expected.type_args):
            for a_arg, e_arg in zip(actual.type_args, expected.type_args):
                if a_arg == e_arg or not isinstance(e_arg, TpyType) or not isinstance(a_arg, TpyType):
                    continue
                e_res = (self._resolve_recursive_refs(e_arg)
                         if isinstance(e_arg, (NominalType, AliasRef)) else e_arg)
                a_res = (self._resolve_recursive_refs(a_arg)
                         if isinstance(a_arg, (NominalType, AliasRef)) else a_arg)
                # Only a *genuine* element difference needs a deep conversion;
                # an AliasRef-vs-resolved-union representation difference (the
                # element is already the union) resolves to equal and is fine.
                if (a_res != e_res and isinstance(e_res, UnionType)
                        and e_res.needs_wrapper()):
                    return CompatError(
                        f"Type mismatch in {context}: cannot convert {actual} "
                        f"element-wise into the expected recursive-union type. "
                        f"Annotate the value with the alias type "
                        f"(e.g. `x: JsonValue = ...`) or pass a literal.", loc)

        # Union-member coercion through generic containers:
        # Container[Lit] -> Container[Expr] when Expr is a recursive union alias
        # and Lit is a valid member. Works for any generic container.
        # The C++ Expr wrapper struct's template constructor handles implicit
        # conversion from Lit to Expr, so Box<Expr>(Lit{...}) compiles.
        if (isinstance(actual, NominalType) and isinstance(expected, NominalType)
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and len(actual.type_args) == len(expected.type_args)
                and actual.type_args != expected.type_args):
            if self._check_union_member_type_args(
                actual, expected, context, loc, source_expr, is_return, coercion_ctx
            ):
                return None

        # Allow T -> Own[T] coercion (ownership transfer)
        if isinstance(expected, OwnType):
            # Warn when lvalue is implicitly copied into owned storage
            # (returns are handled separately as errors in statements.py)
            # Skip warning when auto-move applies (last use of an owned variable).
            # Only locals and Own[T] params are movable -- regular params are borrowed.
            is_auto_moved = self._is_auto_moved(source_expr)
            self.check_own_consumption(source_expr)
            # Mark loop variables as consumed for auto-consuming heuristic
            if isinstance(source_expr, TpyName):
                self.ctx.mark_loop_var_consumed(source_expr.name)
            # A value-tuple with pointer-repr (reference) members copies each
            # such member into owned storage where CPython aliases, even though
            # the tuple itself is a value type -- so it warns under the same
            # lvalue/move guard as the scalar case below. A borrow-carrying call
            # result is an rvalue that the lvalue guard would skip, yet its
            # borrowed members alias the caller and are copied all the same.
            ew = expected.wrapped
            ptr_repr_tuple = (isinstance(ew, TupleType)
                              and ew.has_nested_pointer_repr_element())
            ref_scalar = not ew.is_value_type() and not self._is_value_type_param(ew)
            # A call that hands back a borrow is copied into the owning slot
            # exactly as a name or a field is, so it belongs to the same
            # family. An open type-param payload is no exception: the copy is
            # the same one the monomorphic twin makes, and `copy()` is
            # spellable there now that it takes a readonly source.
            warned_ptr_repr_tuple = False
            if (not is_return and (ref_scalar or ptr_repr_tuple)
                    and self.source_copies_into_storage(source_expr,
                                                        is_auto_moved)):
                if ptr_repr_tuple:
                    before = len(self.ctx.diagnostics)
                    if self.warn_pointer_repr_tuple_copy(
                            source_expr, ew, "owned storage", source_expr):
                        self.record_name_copy(source_expr, None)
                    # A loop var's consuming decision is post-body; defer the
                    # per-member warnings so they can be suppressed if consuming
                    # iteration moves the element (mirrors the scalar case below).
                    if (isinstance(source_expr, TpyName)
                            and source_expr.name in self.ctx.func.loop_vars
                            and source_expr in self.ctx.all_last_uses):
                        for diag_idx in range(before, len(self.ctx.diagnostics)):
                            self.ctx.func.deferred_loop_copy_warnings.setdefault(
                                source_expr.name, []).append(diag_idx)
                    warned_ptr_repr_tuple = True
                else:
                    value_type = self.diag_type(self.ctx.get_expr_type(source_expr))
                    deferred = self.ctx.defer_own_copy_verdict(
                        value_type, expected.wrapped, "owned storage", source_expr)
                    if not deferred and self.ctx.is_type_non_copyable(expected.wrapped):
                        hint = self.nocopy_hint(source_expr)
                        if value_type == expected.wrapped:
                            msg = (f"cannot copy non-copyable type '{expected.wrapped}' "
                                   f"into owned storage{hint}")
                        else:
                            msg = (f"cannot copy {value_type} into owned storage of type "
                                   f"'{expected.wrapped}'; target is non-copyable{hint}")
                        raise self.ctx.error(msg, source_expr)
                    copied = self.pend.list_cells(
                        self.ctx.get_expr_type(source_expr))
                    remedy = self.copy_remedy(source_expr)
                    if not deferred and copied is not None and not copied.settled:
                        # The copied list's element is decided later: the
                        # warning names it as it ends up.
                        list_t = self.ctx.get_expr_type(source_expr)
                        self.pend.defer(
                            source_expr, (copied.tree,),
                            lambda ts: self.ctx.warning(
                                f"copies {self.diag_type(with_container(list_t, ts[0]))} "
                                f"into owned storage; {remedy}", source_expr))
                    elif not deferred:
                        self.ctx.warning(
                            f"copies {value_type} into owned storage; "
                            f"{remedy}",
                            source_expr
                        )
                    if isinstance(unwrap_readonly(unwrap_ref_type(
                            self.ctx.get_expr_type(source_expr))), TupleType):
                        # An element check reached with the whole tuple NAME
                        # as its source: its warning declares that copy.
                        self.record_name_copy(source_expr, None)
                    # For loop variables at last use, the consuming decision is
                    # made post-body. Record the diagnostic index so it can be
                    # suppressed if the loop activates consuming iteration
                    # (elements at last use will be moved, not copied).
                    if (isinstance(source_expr, TpyName)
                            and source_expr.name in self.ctx.func.loop_vars
                            and source_expr in self.ctx.all_last_uses):
                        diag_idx = len(self.ctx.diagnostics) - 1
                        self.ctx.func.deferred_loop_copy_warnings.setdefault(
                            source_expr.name, []).append(diag_idx)
            if warned_ptr_repr_tuple:
                return self._check_compat(actual, ew, context, loc,
                                          source_expr, is_return,
                                          coercion_ctx, target_is_storage_form, sink_owns,
                                          commit)
            # Subclass coercion excluded: Child -> Own[Base] stores Child by value as
            # Base, silently slicing the object. Same invariance as container elements.
            # Only applies when record names differ (different types, not parametric covariance).
            if (isinstance(actual, NominalType) and actual.is_user_record
                    and isinstance(expected.wrapped, NominalType) and expected.wrapped.is_user_record
                    and actual.name != expected.wrapped.name):
                return CompatError(
                    f"Type mismatch in {context}: expected {expected.wrapped}, got {actual}", loc)
            return self._check_compat(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, commit)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self._check_compat(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, commit)

        # Tuple-to-tuple: same length, element-wise compatible
        if isinstance(actual, TupleType) and isinstance(expected, TupleType):
            if len(actual.element_types) != len(expected.element_types):
                e, a = self._pair(expected, actual)
                return CompatError(
                    f"Type mismatch in {context}: expected {e}, got {a} "
                    f"(different tuple lengths)", loc
                )
            for i, (a, e) in enumerate(zip(actual.element_types, expected.element_types)):
                # A tuple ELEMENT is an owning storage slot whatever position
                # the tuple itself sits at: the tuple holds its elements by
                # value, so a borrowing tuple parameter still owns each one.
                # Forwarding the parent's flag judged a buffer element at a
                # tuple ARGUMENT to be borrowing and let a bytearray reach a
                # `bytes` element with no diagnostic at all.
                result = self._check_compat(
                    a, e, f"{context} (tuple element {i})", loc,
                    source_expr=source_expr, is_return=is_return,
                    coercion_ctx=coercion_ctx, target_is_storage_form=True,
                    sink_owns=True, commit=commit,
                )
                if isinstance(result, CompatError):
                    return result
            return None

        # IntLiteral can coerce to BigInt or stay unresolved
        if isinstance(actual, IntLiteralType):
            if is_big_int_type(expected) or isinstance(expected, IntLiteralType):
                return None

        # FloatLiteral can coerce to float/float32 or stay unresolved; a
        # value the float type cannot hold is refused as its row says.
        if isinstance(actual, FloatLiteralType):
            if is_any_float_type(expected):
                row = resolve_coercion(actual, expected, coercion_ctx)
                if (row is not None and row.check_range
                        and not row.check_range(actual, expected)):
                    return CompatError(
                        f"{literal_range_error(actual, expected)} in {context}", loc)
                return None

        # Allow Array element type coercion if sizes match
        if is_array(actual) and is_array(expected):
            if actual.type_args[1] == expected.type_args[1]:
                if isinstance(actual.type_args[0], IntLiteralType) and is_integer_type(expected.type_args[0]):
                    return None

        # Allow list element type coercion
        if is_list(actual) and is_list(expected):
            if isinstance(actual.type_args[0], IntLiteralType) and is_integer_type(expected.type_args[0]):
                return None

        # Allow list -> Array only for literal expressions
        # (global array literals and list repeats become list[T] but can be assigned to Array variables)
        # List *variables* cannot be coerced to Array - codegen can't handle std::vector -> std::array
        if is_list(actual) and is_array(expected):
            a_elem = actual.type_args[0]
            e_elem, e_size = expected.type_args[0], expected.type_args[1]
            if isinstance(source_expr, (TpyArrayLiteral, TpyListRepeat)):
                # Validate size for list repeats with known count
                if isinstance(source_expr, TpyListRepeat) and isinstance(source_expr.count, TpyIntLiteral):
                    repeat_size = len(source_expr.elements) * source_expr.count.value
                    if repeat_size != e_size:
                        return CompatError(
                            f"List repeat produces {repeat_size} elements but Array[..., {e_size}] expects {e_size}",
                            loc
                        )
                if a_elem == e_elem:
                    return None
                if isinstance(a_elem, IntLiteralType) and is_integer_type(e_elem):
                    return None

        # Symmetric to the actual-Pending branch below: the EXPECTED type can
        # itself be a pending container (e.g. appending into a list whose
        # element type is still a pending list literal). Accept a
        # structurally-compatible pending peer before the deferred resolver runs
        # -- via the same `unify_literal_types` the peer-unify path uses, so the
        # two cannot disagree on what matches. The demotion hook converges a
        # jagged peer (different-size nested lists) to vector on the same pass.
        if isinstance(expected, (PendingListType, PendingDictType, PendingSetType)):
            if unify_literal_types(
                    expected, actual,
                    on_pending_pair=(self._demote_pending_pair if commit
                                     else None)) is not None:
                return None
            # A list stored as a row of a list whose leaves cells decide:
            # the store admitted it there (`PendingNums.tree_store`), a
            # typed list deciding the rows' element, a list with cells of
            # its own linked leaf by leaf.
            rows = self.pend.list_cells(expected)
            if rows is not None and self.pend.is_stored_row(rows, actual):
                return None

        # Allow PendingListType compatibility during first phase (before resolution)
        if isinstance(actual, PendingListType):
            # Compatible with list[T] if element types are compatible
            if is_list(expected):
                e_elem = expected.type_args[0]
                # The variable's binding may have been reverted to Unknown by
                # loop_scope even though an in-loop .append() recorded the
                # element on the literal's record. Consult that canonical fact so an
                # in-loop-pinned list is validated against the use site exactly
                # as a straight-line one is; defer to resolve_all only when the
                # element is genuinely still unknown (which resolve_all errors on).
                actual_elem = actual.element_type
                if isinstance(actual_elem, UnknownElementType):
                    canon = self.ctx.list_literal(actual.literal_id)
                    if canon is not None and not isinstance(canon.element_type, UnknownElementType):
                        actual_elem = canon.element_type
                    else:
                        return None
                if actual_elem == e_elem:
                    return None
                if isinstance(actual_elem, IntLiteralType) and is_integer_type(e_elem):
                    return None
                # Element type widening (e.g. int32 -> int32|None, int32 -> int64).
                # Subclass coercion excluded: storing Child in list[Base] silently
                # slices objects (same invariance as dict/set).
                both_records = (
                    isinstance(actual_elem, NominalType) and actual_elem.is_user_record
                    and isinstance(e_elem, NominalType) and e_elem.is_user_record
                )
                if both_records:
                    # Explicit error: avoid leaking PendingList internal repr in the generic message.
                    e, a = self._pair(e_elem, actual_elem)
                    return CompatError(
                        f"Type mismatch in {context}: expected {e}, got {a}", loc)
                else:
                    # Element type widening (e.g. int32 -> int32|None, int32 -> int64).
                    # Container element slot is storage form -- no address-take
                    # mark should fire even if the inner type is pointer-repr Optional.
                    result = self._check_compat(
                        actual_elem, e_elem,
                        context, loc, source_expr, is_return, coercion_ctx,
                        target_is_storage_form=True, sink_owns=True,
                        commit=commit,
                    )
                    if not isinstance(result, CompatError):
                        return None  # element coercion is a probe, not propagated
                    # Definite mismatch: report it cleanly (see both_records above).
                    e, a = self._pair(e_elem, actual_elem)
                    return CompatError(
                        f"Type mismatch in {context}: expected {e}, got {a}", loc)
            # Compatible with Array[T, N] if element types and sizes match
            if is_array(expected):
                if self.type_ops and self.type_ops.pending_list_matches_array(
                        actual, expected, commit=commit):
                    return None
                # Specific error for list repeat size mismatch
                e_size = expected.type_args[1]
                if (isinstance(source_expr, TpyListRepeat)
                        and actual.size >= 0 and actual.size != e_size):
                    return CompatError(
                        f"List repeat produces {actual.size} elements but "
                        f"Array[..., {e_size}] expects {e_size}",
                        loc,
                    )
            # Inline repeat cannot be passed directly to Span -- assign to a variable first
            if is_span(expected) and isinstance(source_expr, TpyListRepeat):
                return CompatError(
                    f"Cannot pass list repeat directly to {expected}: "
                    f"assign to a variable first",
                    loc,
                )

        # ListRepeatType materializes to list[T] or Array[T, N]
        if isinstance(actual, ListRepeatType):
            if is_list(expected):
                e_elem = expected.type_args[0]
                if actual.element_type == e_elem:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and is_integer_type(e_elem):
                    return None
            if is_array(expected):
                if actual.element_type == expected.type_args[0]:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and is_integer_type(expected.type_args[0]):
                    return None

        # Allow PendingDictType compatibility during first phase (before resolution).
        # Consult the literal's record for a key/value the binding lost to a loop_scope
        # revert (see the PendingList branch above); defer only when genuinely
        # unknown, else report the mismatch cleanly here.
        written = self._written_arg_entries(actual, expected)
        if written is not None:
            # Every value written in the literal must fit the part of the
            # container it is written at, as when the literal is analyzed
            # at that container.
            for step, want in enumerate(expected.type_args[:len(written[0])]):
                if self.pend.first_unfit([e[step] for e in written],
                                         want) is not None:
                    return CompatError(
                        f"Type mismatch in {context}: expected {expected}, "
                        f"got {self.diag_type(actual)}", loc)
            return None

        if isinstance(actual, PendingDictType) and is_dict(expected):
            e_k, e_v = expected.type_args[0], expected.type_args[1]
            canon = self.ctx.container_record(actual.literal_id)
            a_k = actual.key_type
            if isinstance(a_k, UnknownElementType) and canon is not None:
                a_k = canon.key_type
            a_v = actual.value_type
            if isinstance(a_v, UnknownElementType) and canon is not None:
                a_v = canon.value_type
            key_ok = isinstance(a_k, UnknownElementType) or _container_elem_matches(a_k, e_k)
            val_ok = isinstance(a_v, UnknownElementType) or _container_elem_matches(a_v, e_v)
            if key_ok and val_ok:
                return None
            return CompatError(
                f"Type mismatch in {context}: expected {expected}, got dict[{a_k}, {a_v}]", loc)

        # Allow PendingSetType compatibility during first phase (before resolution).
        if isinstance(actual, PendingSetType) and is_set(expected):
            canon = self.ctx.container_record(actual.literal_id)
            a_e = actual.element_type
            if isinstance(a_e, UnknownElementType) and canon is not None:
                a_e = canon.element_type
            if isinstance(a_e, UnknownElementType) or _container_elem_matches(a_e, expected.type_args[0]):
                return None
            return CompatError(
                f"Type mismatch in {context}: expected {expected}, got set[{a_e}]", loc)

        # DictType compatibility: key and value types must match exactly.
        # Own[V] stripping is handled by _container_elem_matches.
        # Subclass coercion is intentionally excluded: dict values are stored by value,
        # and tpy::dict_update/tpy::dict_ctor are non-converting templates (invariant V).
        if is_dict(actual) and is_dict(expected):
            a_k, a_v = actual.type_args[0], actual.type_args[1]
            e_k, e_v = expected.type_args[0], expected.type_args[1]
            key_ok = _container_elem_matches(a_k, e_k)
            val_ok = _container_elem_matches(a_v, e_v)
            if key_ok and val_ok:
                return None
            # Raise a specific error pointing at the mismatching element type.
            # Strip Own[V] from the message to avoid leaking implementation details.
            check_val = e_v.wrapped if isinstance(e_v, OwnType) else e_v
            if not key_ok:
                ek, ak = self._pair(e_k, a_k)
                return CompatError(
                    f"Type mismatch in {context}: expected {ek}, got {ak}", loc)
            ev, av = self._pair(check_val, a_v)
            return CompatError(
                f"Type mismatch in {context}: expected {ev}, got {av}", loc)

        # SetType compatibility: element types must match exactly.
        # Subclass coercion is intentionally excluded: tpy::ordered_set<T> is a
        # non-converting template (invariant T).
        if is_set(actual) and is_set(expected):
            a_elem, e_elem = actual.type_args[0], expected.type_args[0]
            if _container_elem_matches(a_elem, e_elem):
                return None
            check_elem = e_elem.wrapped if isinstance(e_elem, OwnType) else e_elem
            ce, ae = self._pair(check_elem, a_elem)
            return CompatError(
                f"Type mismatch in {context}: expected {ce}, got {ae}", loc)

        # Allow PendingStrType compatibility during first phase (before resolution)
        if isinstance(actual, PendingStrType):
            if is_any_str_type(expected):
                return None
            if isinstance(expected, LiteralType) and expected.is_str_base():
                return None
        if isinstance(expected, PendingStrType):
            if is_str_category(actual):
                return None
        # LiteralType is compatible with its base type family
        if isinstance(actual, LiteralType) and isinstance(expected, LiteralType):
            if all(v in expected.values for v in actual.values):
                return None
            # Reject explicitly: skipping the permissive str/int branches below
            # is what enforces value-set membership.
            return CompatError(
                f"Type mismatch in {context}: expected {expected}, got {actual}", loc)
        if isinstance(expected, LiteralType):
            src_lit = _literal_value_from_source(source_expr, expected)
            if src_lit is not None:
                if src_lit in expected.values:
                    return None
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, "
                    f"got {src_lit}", loc)
            if isinstance(actual, IntLiteralType) and actual.value is not None and expected.is_int_base():
                if expected.contains(LiteralTag.INT, actual.value):
                    return None
                return CompatError(
                    f"Type mismatch in {context}: expected {expected}, "
                    f"got {actual.value}", loc)
            # Non-literal source flowing into a Literal[...] LHS is rejected:
            # sema cannot prove the runtime value is in the declared set, and
            # the LHS's narrower type drives downstream Literal-specialized
            # dispatch (which would run the wrong arm on an out-of-set value).
        if isinstance(actual, LiteralType):
            if actual.is_str_base() and is_str_category(expected):
                return None
            if actual.base_type == expected:
                return None
            if actual.is_int_base() and is_integer_type(expected):
                return None
        # Allow PendingBytesType compatibility during first phase (before resolution)
        if isinstance(actual, PendingBytesType):
            if is_bytes_category(expected) or isinstance(expected, PendingBytesType):
                return None
        if isinstance(expected, PendingBytesType):
            if is_bytes_category(actual):
                return None

        ctx = coercion_ctx
        coercion = resolve_coercion(actual, expected, ctx, sink_owns)
        if coercion is None:
            # Inheritance: Child -> Ptr[Parent] / Ptr[readonly[Parent]] (address-of with upcast).
            # `_is_covariant_target` covers both class inheritance (Child -> Ptr[ParentRecord])
            # and @dynamic-protocol implementation (Child -> Ptr[@dynamic Protocol]).
            #
            # Also accepts `actual` being a @dynamic protocol value (lowered as
            # `Base&` already): `&handle` produces `Base*` natively, matching
            # `Ptr[same protocol]`. Identity is the common case (`P -> Ptr[P]`
            # inside a function that took the protocol value as a param);
            # protocol-to-parent-protocol upcasts also work via the existing
            # covariance check.
            actual_is_addr_taker = (
                isinstance(actual, NominalType)
                and (actual.is_user_record or is_dyn_protocol(actual))
            )
            if actual_is_addr_taker:
                # Identity shortcut (actual == inner_pointee) only fires for
                # @dynamic protocol values: their `Base&`-style lowering needs
                # the value->Ptr path without going through resolve_coercion
                # (no `protocol_to_ptr` rule exists). User-record identity is
                # handled by the named `record_to_ptr` rule in resolve_coercion;
                # gating the shortcut keeps the coercion AST name stable so
                # downstream code can rely on it.
                actual_is_dyn = is_dyn_protocol(actual)
                if isinstance(expected, PtrType) and not expected.is_readonly and isinstance(expected.inner_pointee, NominalType):
                    if ((actual_is_dyn and actual == expected.inner_pointee)
                            or self._is_covariant_target(actual, expected.inner_pointee)):
                        coercion = UPCAST_TO_PTR
                elif is_readonly_ptr(expected) and isinstance(expected.inner_pointee, NominalType):
                    if ((actual_is_dyn and actual == expected.inner_pointee)
                            or self._is_covariant_target(actual, expected.inner_pointee)):
                        coercion = UPCAST_TO_CONST_PTR
        if coercion is None:
            # Address-of a reference-bound generic param: `&fp` yields `W*` =
            # `Ptr[W]`. Identity only (same type param), mirroring the @dynamic
            # value->Ptr shortcut above. A concrete user-record param already
            # takes this path via `record_to_ptr`; this extends it to the
            # abstract `W` so a generic holder can store `Ptr[W]` of its param.
            # An INT type param is a std::size_t value, not addressable here.
            # A value-bounded `W` is passed `const W&`, so `&fp` is `const W*`:
            # only the readonly Ptr is sound; a mutable `Ptr[W]` falls through
            # to the type-mismatch error rather than emitting `const W* -> W*`.
            if (isinstance(actual, TypeParamRef) and actual.kind != TypeParamKind.INT
                    and isinstance(expected, PtrType) and expected.inner_pointee == actual):
                if expected.is_readonly:
                    coercion = UPCAST_TO_CONST_PTR
                elif not actual.is_value_type():
                    coercion = UPCAST_TO_PTR
        if coercion is None:
            # __span__() method coercion: type with __span__() -> Span[T] coerces to Span/Span[readonly[T]]
            if isinstance(actual, NominalType) and is_span(expected):
                span_ret = get_span_return_type(actual, registry=self.ctx.registry)
                if span_ret is not None and unwrap_readonly(span_ret.type_args[0]) == unwrap_readonly(expected.type_args[0]):
                    # Span[readonly[T]] cannot coerce to mutable Span
                    if not is_readonly_span(span_ret) or is_readonly_span(expected):
                        if ctx == CoercionContext.ARG:
                            coercion = SPAN_METHOD_TO_SPAN_ARG
                        else:
                            coercion = SPAN_METHOD_TO_SPAN
        if coercion is None:
            # Spannable[T] protocol -> Span[readonly[T]] coercion via __span__()
            if (is_protocol_type(actual) and actual.qualified_name() == "tpy.Spannable"
                    and actual.type_args and is_span(expected)
                    and is_readonly_span(expected) and actual.type_args[0] == unwrap_readonly(expected.type_args[0])):
                if ctx == CoercionContext.ARG:
                    coercion = SPAN_METHOD_TO_SPAN_ARG
                else:
                    coercion = SPAN_METHOD_TO_SPAN
        if coercion is None:
            # Generic deref coercion: any type with __deref__() -> T coerces to T
            if self.type_ops:
                deref_target = self.type_ops.get_deref_coercion_target(actual)
                if deref_target is None and is_readonly_ptr(actual):
                    # Ptr[readonly[T]] -> T via deref is safe for returns and
                    # assignments (the value is copied); reject only in ARG
                    # context where the callee may need a mutable reference.
                    if ctx != CoercionContext.ARG:
                        deref_target = self.type_ops.get_deref_target_type(actual)
                if deref_target is not None and unwrap_readonly(deref_target) == expected:
                    coercion = DEREF_COERCION
        if coercion is None:
            e, a = self._pair(expected, actual)
            return CompatError(
                f"Type mismatch in {context}: expected {e}, got {a}"
                f"{self._conversion_hint(actual, expected, ctx, sink_owns)}",
                loc)

        if commit and isinstance(actual, PendingListType) and is_span(expected):
            info = self.ctx.list_literal(actual.literal_id)
            if info:
                info.coerced_element_type = expected.type_args[0]
                info.passed_to_span_param = True

        if coercion.check_range and not coercion.check_range(actual, expected):
            return CompatError(
                f"{literal_range_error(actual, expected)} in {context}", loc)

        if coercion.requires_mutable_lvalue:
            if source_expr is None or not self.is_mutable_lvalue(source_expr):
                return CompatError(
                    f"Cannot take mutable pointer to read-only or temporary value in {context}; "
                    f"use a read-only pointer for read-only access, or assign to a variable first",
                    loc
                )
            # Address-taking coercion (record -> Ptr[Record]) requires T& binding.
            # Track so that codegen can't safely emit const T& for this param.
            if isinstance(source_expr, TpyName):
                root = self.ctx.func.borrow_tracker.effective_storage(source_expr.name)
                self.ctx.mark_param_mutated(root)
        elif coercion.requires_lvalue:
            if source_expr is None or not self.is_lvalue(source_expr):
                return CompatError(
                    f"Cannot take address of a temporary or expression in {context}; "
                    f"assign to a variable first",
                    loc
                )
            # Mutable Span from a lvalue container (e.g. Array -> Span[T])
            # requires non-const source; codegen calls as_mut_span().
            # Mark source param as T&. Unlike the pointer-repr Optional
            # storage-form gate above, this mark applies even for
            # storage-form Span destinations -- the assignment still
            # emits as_mut_span(source), which can't bind to a const
            # source.
            if (is_span(expected) and not is_readonly_span(expected)
                    and source_expr is not None):
                self._mark_addr_taken(source_expr)

        if coercion.forbid_return_local and is_return:
            if source_expr is not None and self.is_dangling_return(source_expr):
                return CompatError(
                    f"Cannot return local or temporary value; "
                    f"the returned pointer/reference would dangle",
                    loc
                )

        return coercion

    def _borrow_coro_awaited_inner(self, expr: TpyExpr,
                                   actual_bare: TpyType) -> 'TpyType | None':
        """The awaited inner type when `expr` is a borrow-returning
        coroutine (a direct async-def call result, or a bound concrete
        handle carrying `result_is_borrow`); None otherwise. Consumed by
        the erasure-boundary reject in `coerce_expr`."""
        inner_t = (actual_bare.wrapped if isinstance(actual_bare, OwnType)
                   else actual_bare)
        inner_t = unwrap_readonly(inner_t)
        if isinstance(inner_t, ConcreteCoroType):
            return inner_t.type_args[0] if inner_t.result_is_borrow else None
        if not (isinstance(inner_t, NominalType)
                and inner_t.qualified_name() == qnames.CANCELLABLE
                and inner_t.type_args):
            return None
        fi = getattr(expr, "resolved_function_info", None)
        if fi is None or not getattr(fi, "is_async", False):
            return None
        awaited = inner_t.type_args[0]
        return (awaited
                if async_result_aliases(fi.async_inner_return, awaited)
                else None)

    def materialize_fresh_value(
        self, expr: TpyExpr, actual: TpyType, expected: TpyType, context: str,
        coercion: Optional[Coercion], coercion_ctx: CoercionContext,
        target_is_storage_form: bool = False,
    ) -> TpyExpr:
        """The node to store back for a sink that emits its operand against the
        expected C++ type WITHOUT applying the coercion -- an aggregate literal
        element, a dict lookup key. A `builds_fresh_value` rule has no implicit
        C++ conversion, so a coercion left off the node is silently dropped
        there (see that `Coercion` flag); every other rule is C++-implicit or
        applied by the sink's own codegen, so spelling it here would
        double-convert. `coercion` is what the sink's compatibility check
        already resolved -- this decides only whether to spell it."""
        if coercion is None or not coercion.builds_fresh_value:
            return expr
        return self.coerce_expr(expr, actual, expected, context,
                                coercion_ctx=coercion_ctx,
                                target_is_storage_form=target_is_storage_form)

    def coerce_expr(
        self, expr: TpyExpr, actual: TpyType, expected: TpyType, context: str,
        coercion_ctx: CoercionContext, is_return: bool = False,
        target_is_storage_form: bool = False,
    ) -> TpyExpr:
        """Wrap expr in a coercion node if a conversion is needed.

        target_is_storage_form: see _check_compat docstring. Set by callers
        that know the destination is a field / container element so the
        pointer-repr-Optional address-take mark is suppressed.
        """
        # The slot receives the callable a class-name factory stands for, so
        # the node the caller stores back is that lambda.
        expr = lambda_of(expr)
        pending = self._pending_num_ends(actual, expected, expr, context)
        if pending is not None:
            return (self.pend.coerce(expr, strip_int(actual),
                                     strip_int(expected), context, coercion_ctx)
                    if pending else expr)
        actual = self.pend.current(actual)
        expected = self.pend.current(expected)
        # A bound coroutine handle (owned-erased Own[Cancellable[T]]) is
        # single-use and consume-only: borrowing it into a bare-protocol
        # slot (protocol-annotated local, borrow param) has no supported
        # form -- codegen would alias the unique_ptr, breaking the
        # move-consume discipline. Own[...] destinations (create_task,
        # forwarding) are the consuming path and stay allowed.
        actual_bare = unwrap_readonly(unwrap_send_sync(actual))
        if isinstance(actual_bare, OwnType):
            inner = unwrap_readonly(actual_bare.wrapped)
            expected_bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(expected)))
            # Handing a bound coroutine frame to an owning slot (a task, an
            # Own[Cancellable[T]] param) moves the frame.
            if (isinstance(inner, ConcreteCoroType)
                    and isinstance(unwrap_send_sync(unwrap_readonly(expected)),
                                   OwnType)):
                self.ctx.check_coro_move_unstarted(expr, f"into {context}")
            if (isinstance(inner, NominalType)
                    and inner.qualified_name() == qnames.CANCELLABLE
                    and is_protocol_type(expected_bare)
                    and not isinstance(unwrap_send_sync(unwrap_readonly(expected)), OwnType)):
                raise self.ctx.error(
                    f"cannot borrow a coroutine handle into {context}: the "
                    f"handle is single-use; consume it instead (await it, "
                    f"pass it to asyncio.create_task/run, or move it into "
                    f"an Own[Cancellable[T]] slot)",
                    expr if getattr(expr, "loc", None) else None)
        # A borrow-returning coroutine is direct-await-only: its Poll
        # payload is a pointer into caller-durable storage, but every
        # erased/templated awaitable surface (Own[Cancellable[T]] task
        # slots, Awaitable/Cancellable-typed params) promises an owned
        # payload the task layer can park past the source's lifetime.
        borrowed_inner = self._borrow_coro_awaited_inner(expr, actual_bare)
        if borrowed_inner is not None:
            expected_own = unwrap_readonly(unwrap_send_sync(expected))
            exp_inner = (unwrap_readonly(expected_own.wrapped)
                         if isinstance(expected_own, OwnType)
                         else unwrap_readonly(unwrap_ref_type(expected_own)))
            if (isinstance(exp_inner, NominalType)
                    and exp_inner.qualified_name() in (qnames.CANCELLABLE,
                                                       qnames.AWAITABLE)
                    and (isinstance(expected_own, OwnType)
                         or coercion_ctx in (CoercionContext.ARG,
                                             CoercionContext.RETURN))):
                raise self.ctx.error(
                    f"cannot pass a borrow-returning coroutine to {context}: "
                    f"'-> {borrowed_inner}' makes the awaited result a "
                    f"borrow of the caller's data, which is only valid at a "
                    f"direct 'await'. This consumer stores an owned result: "
                    f"declare the async def '-> Own[{borrowed_inner}]' and "
                    f"return an owned value (tpy.copy(...) a borrowed "
                    f"source such as 'self')",
                    expr if getattr(expr, "loc", None) else None)
        coercion = self.check_type_compatible(
            actual, expected, context,
            getattr(expr, "loc", None),
            source_expr=expr,
            is_return=is_return,
            coercion_ctx=coercion_ctx,
            target_is_storage_form=target_is_storage_form,
        )
        if coercion is None:
            return expr
        # Value-to-mutable-Ptr coercions (`record_to_ptr`, `upcast_to_ptr`)
        # emit `&expr` and require the source storage to stay mutable -- same
        # propagation the explicit `take_ptr(x)` path gets via the
        # `value_ptr_coercion` handler in calls.py and the vararg pack handler.
        if coercion.name in ("record_to_ptr", "upcast_to_ptr"):
            self._mark_addr_taken(expr)
        runtime_bigint = False
        if coercion.name == "int_literal_to_fixed_int":
            runtime_bigint = self.is_runtime_bigint_expr(expr)
        # INTO_ANY needs a concrete actual_type for codegen (the storage
        # typeid). PendingListType / PendingDictType / PendingSetType
        # would normally resolve later via coerced_element_type, but that
        # signal is set by typed targets -- Any provides no element
        # constraint. Default-resolve here so the cell's typeid is stable.
        if coercion.name == "into_any":
            actual = self._resolve_pending_for_any_storage(actual)
        coerced = TpyCoerce(
            expr=expr,
            actual_type=actual,
            expected_type=expected,
            coercion=coercion,
            context_kind=coercion_ctx,
            context_msg=context,
            runtime_bigint=runtime_bigint,
            loc=expr.loc
        )
        self.ctx.set_expr_type(coerced, expected)
        return coerced

    def coerce_reassignment(
        self, name: str, existing_type: TpyType, value_type: TpyType,
        value_expr: TpyExpr, err_node,
    ) -> tuple[TpyType, TpyExpr]:
        """Single entry point for local-reassignment compatibility, so the
        per-shape rules can't drift between the var-decl and walrus paths.

        Returns (resolved_target_type, coerced_value_expr); raises on an
        incompatible RHS. Callers keep their own var_types / declared-type
        sync and storage bookkeeping.
        """
        inner_existing = unwrap_readonly(existing_type)
        inner_value = unwrap_readonly(value_type)
        ctx = f"reassignment to '{name}'"

        if isinstance(inner_existing, PendingGenericInstanceType):
            raise self.ctx.error(
                f"Cannot reassign '{name}' while its generic type is still "
                f"being inferred; add explicit type arguments to the constructor",
                err_node)

        # PendingList: track size (list-vs-array decision) and check element
        # compatibility -- coerce_expr can't model pending-list-vs-pending-list,
        # so the element check lives here.
        # A container literal whose cells decide its leaves: one local, one
        # element type.
        lc = self.pend.container_cells(inner_existing)
        if lc is not None:
            # One local, one numeric family: asked of the leaves as the
            # container holds them so far.
            self.deduction.refuse_int_float_rebind(
                name, self.pend.so_far(inner_existing, lc),
                inner_value, value_expr, site=err_node)
        if isinstance(inner_existing, PendingListType):
            if self.deduction.rebind_elem_cell(inner_existing, inner_value,
                                               err_node):
                self._demote_or_link_pending_lists(
                    inner_existing, pending_list_of(inner_value))
                return existing_type, value_expr
            self.deduction.refuse_int_float_rebind(
                name, inner_existing, inner_value, value_expr, site=err_node)
            if isinstance(inner_value, PendingListType):
                self._demote_or_link_pending_lists(inner_existing, inner_value)
            err = self._reassign_list_element_compat(
                inner_existing, inner_value, value_expr, ctx)
            if err is not None:
                raise self.ctx.error(err.message, err_node)
            return existing_type, value_expr

        if lc is not None:
            if self.deduction.rebind_elem_cell(inner_existing, inner_value,
                                               err_node):
                return existing_type, value_expr
            inner_existing = self.pend.so_far(inner_existing, lc)

        # str/bytes view family: track owned-vs-borrow provenance (so an owned
        # source promotes the local to owned storage) before coercing. Unwrap
        # Own for the family check -- an Own[bytes] source (e.g. concat) is a
        # family member and an owned source par excellence; leaving it wrapped
        # would skip the promotion and leave a view of a dying temporary
        # (mirrors the statement-path unwrap in _analyze_assign).
        if (isinstance(inner_existing, (PendingViewType, LiteralType))
                and (vf := view_family_for_type(inner_existing)) is not None):
            inner_value_owned = unwrap_own(inner_value)
            if vf.is_any_member(inner_value_owned):
                if not self.deduction.is_view_compatible_source(value_expr, inner_value_owned):
                    self.deduction.mark_view_reassigned_from_owned(name, vf)
                else:
                    self.deduction.track_view_reassign_source(name, inner_value_owned, vf)
                    self.deduction.mark_view_nonstatic_reassign(name, value_expr, vf)
                    self.deduction.note_view_rebind(name, value_expr, vf)
            coerced = self.coerce_expr(
                value_expr, inner_value_owned, inner_existing, ctx,
                coercion_ctx=CoercionContext.ASSIGN)
            return existing_type, coerced

        # Non-pending: resolve the target type, then either upgrade an
        # IntLiteral-seeded local (no coerce -- caller syncs var_types) or
        # coerce the RHS against the RESOLVED type, not the annotation: a borrow
        # rebind of a per-element-Own tuple local must not warn a copy into
        # owned storage.
        var_type = self.deduction.resolve_reassignment_target_type(
            name, inner_existing, inner_value, init_expr=value_expr)
        if is_pending_num(inner_value) and is_numeric_slot(var_type):
            # Whether the value is wider than the local's first type is known
            # once the function settles.
            typed_local = (self.deduction.declared_slot_type(name, inner_existing)
                           is None)
            return var_type, self.pend.coerce(
                value_expr, strip_int(inner_value), var_type, ctx,
                CoercionContext.ASSIGN,
                rebind_of=name if typed_local else None)
        coerced = value_expr
        if not (isinstance(inner_existing, IntLiteralType) and is_integer_type(var_type)):
            coerced = self.coerce_expr(
                value_expr, inner_value, var_type, ctx,
                coercion_ctx=CoercionContext.ASSIGN)
        # Readonly status flows from the value expression.
        if isinstance(value_type, ReadonlyType) and not var_type.is_value_type():
            if isinstance(var_type, OptionalType):
                var_type = OptionalType(ReadonlyType(var_type.inner))
            else:
                var_type = ReadonlyType(var_type)
        return var_type, coerced

    def _reassign_list_element_compat(
        self, existing_pl: PendingListType, value_type: TpyType,
        value_expr: TpyExpr, context: str,
    ) -> 'CompatError | None':
        """Element compatibility for reassigning a PendingList local.

        Permissive on widening (accepts either coercion direction) to avoid
        regressing the prior accept-everything behavior; the point is only to
        catch cross-category mismatches (list[int] <- list[str] / non-list).
        The display-typed message avoids leaking the internal PendingList repr.
        """
        loc = getattr(value_expr, "loc", None)
        info = self.ctx.list_literal(existing_pl.literal_id)
        # The literal's info is the authority on the element type: an empty `[]`
        # carries UNKNOWN_ELEMENT in the type and learns from its uses.
        existing_raw = (info.element_type if info is not None
                        else existing_pl.element_type)
        lc = self.pend.list_cells(existing_pl)
        if lc is not None:
            existing_raw = self.pend.tree_known(lc).element_type
        new_elem_raw = self._list_like_element(value_type)
        if (new_elem_raw is None and isinstance(value_type, RefType)
                and self._list_like_element(value_type.wrapped) is not None):
            return CompatError(
                f"Type mismatch in {context}: it holds a "
                f"list[{self._default_resolve_element(existing_raw)}] of its "
                f"own and cannot be rebound to share an existing "
                f"{value_type.wrapped}", loc)
        if new_elem_raw is None:
            return CompatError(
                f"Type mismatch in {context}: expected "
                f"list[{self._default_resolve_element(existing_raw)}], "
                f"got {value_type}", loc)
        if isinstance(existing_raw, UnknownElementType):
            # `xs = []` then `xs = [1, 2]`: one local, one element type -- the
            # binding that knows it teaches the one that does not, exactly as
            # an `xs.append(1)` would.
            if info is not None:
                self.deduction.update_list_element_type(info, new_elem_raw)
            return None
        existing_elem = self._default_resolve_element(existing_raw)
        new_elem = self._default_resolve_element(new_elem_raw)
        if existing_elem == new_elem:
            return None
        # source_expr=None: these probe the *element* types, so the list RHS
        # node must not drive _check_compat's expr-identity side effects
        # (set_expr_type / mutable-lvalue marking).
        fwd = self._check_compat(new_elem, existing_elem, context, loc, None,
                                 False, CoercionContext.ASSIGN,
                                 target_is_storage_form=True)
        if not isinstance(fwd, CompatError):
            return None
        bwd = self._check_compat(existing_elem, new_elem, context, loc, None,
                                 False, CoercionContext.ASSIGN,
                                 target_is_storage_form=True)
        if not isinstance(bwd, CompatError):
            return None
        return CompatError(
            f"Type mismatch in {context}: expected list[{existing_elem}], "
            f"got list[{new_elem}]", loc)

    def _list_like_element(self, t: TpyType) -> 'TpyType | None':
        if isinstance(t, PendingListType):
            return t.element_type
        if is_list(t) or is_array(t):
            return t.type_args[0]
        return None

    def _pending_list_is_list(self, p: PendingListType) -> bool:
        """Whether a pending list is already forced to `list` (e.g. internally
        jagged): its sibling must then become `list` too, since codegen emits
        every sibling of a homogeneous container against one C++ element type."""
        info = self.ctx.list_literal(p.literal_id)
        return info is not None and info.is_mutated

    def _demote_or_link_pending_lists(self, a: PendingListType, b: PendingListType) -> None:
        """Reconcile two pending lists for the REASSIGNMENT path (`coerce_reassignment`):
        different sizes (or either side already a `list`) can't share a fixed
        `Array`, so demote both to `list` (vector); otherwise link so a later
        demotion of either propagates to the other (one local aliasing two literals).

        The peer-unify path uses `_demote_pending_pair` instead, which deliberately
        does NOT link: container peers converge via codegen element-targeting, and
        linking equal-size peers there mis-fired `link_list_literals`' multi-source
        demotion (the uniform-nesting over-demotion). The link is only sound here
        because reassignment links a single top-level pair, never recursively."""
        if (a.size != b.size
                or self._pending_list_is_list(a) or self._pending_list_is_list(b)):
            self.deduction.mark_list_different_size(a.literal_id)
            self.deduction.mark_list_different_size(b.literal_id)
        else:
            self.deduction.link_list_literals(a.literal_id, b.literal_id)

    def _demote_pending_pair(self, a: TpyType, b: TpyType) -> None:
        """`on_pending_pair` hook for `unify_literal_types`, the one place a
        matched sibling pending-LIST pair's records are reconciled: demote the
        pair to `list` (vector) when they can't share a fixed `Array` --
        different sizes, or either already forced to `list` -- and give an
        empty unbound peer (`[[], ["a"]]`) its sibling's element, the record
        side of unify answering the pair with the non-empty one. That copies
        the element as it is now; it links nothing.

        `unify_literal_types` fires this at every pending pair it matches on its
        traversal (inner pairs first), so a jagged level propagates to its
        equal-size peers wherever a pending list is reachable: bare, or nested in
        a tuple / dict value / outer list. No alias link here: peers of a
        homogeneous container converge through the container's element type at
        codegen, so equal-size unmutated peers need no link -- and linking them
        would mis-fire `link_list_literals`' multi-source demotion and wrongly
        demote a uniform nesting (`[[[1, 2], [3, 4]], [[5, 6], [7, 8]]]`). Pending
        dict/set pairs have no Array-vs-list axis; the recursion in unify is
        enough."""
        if (isinstance(a, PendingListType) and isinstance(b, PendingListType)
                and (a.size != b.size
                     or self._pending_list_is_list(a) or self._pending_list_is_list(b))):
            self.deduction.mark_list_different_size(a.literal_id)
            self.deduction.mark_list_different_size(b.literal_id)
        if isinstance(a, PendingListType) and isinstance(b, PendingListType):
            for empty, full in ((a, b), (b, a)):
                info = self.ctx.list_literal(empty.literal_id)
                if (info is not None and info.elem_cells is None
                        and isinstance(info.element_type, UnknownElementType)
                        and not isinstance(full.element_type,
                                           UnknownElementType)
                        and info.variable_name is None):
                    info.element_type = full.element_type

    def _resolve_pending_for_any_storage(self, actual: TpyType) -> TpyType:
        """Convert Pending{List,Dict,Set}Type to its concrete container
        form using the literal-info's inferred element types, defaulting
        IntLiteralType / FloatLiteralType to the configured defaults.
        """
        if isinstance(actual, PendingListType):
            elem = self._default_resolve_element(actual.element_type)
            info = self.ctx.list_literal(actual.literal_id)
            if info is not None and info.coerced_element_type is None:
                info.coerced_element_type = elem
            return make_list(elem)
        if isinstance(actual, PendingDictType):
            k = self._default_resolve_element(actual.key_type)
            v = self._default_resolve_element(actual.value_type)
            return make_dict(k, v)
        if isinstance(actual, PendingSetType):
            elem = self._default_resolve_element(actual.element_type)
            return make_set(elem)
        return actual

    def _default_resolve_element(self, t: TpyType) -> TpyType:
        if isinstance(t, IntLiteralType):
            return self.ctx.default_int_type
        if isinstance(t, FloatLiteralType):
            return FLOAT
        return t

    def is_runtime_bigint_expr(self, expr: TpyExpr) -> bool:
        """Check if an IntLiteralType expression could be BigInt at runtime."""
        if isinstance(expr, TpyCoerce):
            return self.is_runtime_bigint_expr(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return self.is_runtime_bigint_expr(expr.left) or self.is_runtime_bigint_expr(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self.is_runtime_bigint_expr(expr.operand)
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            return True
        return True

    def is_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is an lvalue (can have its address taken)."""
        if isinstance(expr, TpyCoerce):
            return self.is_lvalue(expr.expr)
        # Named variables are lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on an lvalue is also an lvalue (e.g., obj.field)
        if isinstance(expr, TpyFieldAccess):
            return self.is_lvalue(expr.obj)
        # Subscript on an lvalue is also an lvalue (e.g., arr[i])
        if isinstance(expr, TpySubscript):
            return self.is_lvalue(expr.obj)
        # A non-value ternary or and/or select is the classifier's: an lvalue
        # unless every operand is fresh (a fresh operand beside an lvalue one
        # is emplaced into a slot).
        # So an owning slot copies the chosen arm and warns, and a local
        # bound from it borrows rather than owns.
        if isinstance(expr, TpyIfExpr) or (
                isinstance(expr, TpyBinOp) and expr.op in ("&&", "||")):
            rt = self.ctx.get_expr_type(expr)
            if rt is not None and not rt.is_value_type():
                return not is_rvalue_source(self.ctx, expr)
        if isinstance(expr, TpyIfExpr):
            return self.is_lvalue(expr.then_expr) and self.is_lvalue(expr.else_expr)
        # A `@property` read is a method call from the moment sema resolves
        # it, so node kind no longer separates it from the field read it
        # wraps -- the getter's RETURN CONVENTION does. One that hands back a
        # reference into its receiver's storage is the field access arm
        # above, spelled as a call, and answers the same way: an lvalue
        # exactly when the receiver it reaches through is one.
        if property_access_returns_cpp_ref(self.ctx, expr):
            return self.is_lvalue(expr.obj)
        # Everything else (calls, literals, operators) are rvalues
        return False

    def is_const_ref_source(self, expr: TpyExpr) -> bool:
        """Check if expression provides a const reference (can't be captured as T&).

        Returns True for sources that are inherently const in C++:
        - Field access / subscript through a readonly-typed object
        """
        if isinstance(expr, TpyCoerce):
            return self.is_const_ref_source(expr.expr)
        if isinstance(expr, TpySubscript):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if obj_type is not None:
                unwrapped = unwrap_readonly(obj_type)
                if is_span(unwrapped) and is_readonly_span(unwrapped):
                    return True
            return self.is_const_ref_source(expr.obj)
        if isinstance(expr, TpyFieldAccess):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if obj_type is not None and isinstance(obj_type, ReadonlyType):
                return True
            return self.is_const_ref_source(expr.obj)
        if isinstance(expr, TpyName):
            expr_type = self.ctx.get_expr_type(expr)
            if expr_type is not None and isinstance(expr_type, ReadonlyType):
                return True
        return False

    def _is_auto_moved(self, source_expr: 'TpyExpr | None') -> bool:
        """Last-use of an owned local: auto-move makes the copy invisible."""
        return self.is_auto_move_use(source_expr)

    def _dead_handle_value(self, name: TpyName) -> 'TpyExpr | None':
        """The value a last-use local HANDLE (an iterator or view over
        other storage) lends its elements from, or None when the local's
        type proves its elements owned or it is no handle at all. Keyed on
        the TYPE, not on the loans the binding filed: an alias (`w = z`) or
        an unpack target files none, yet lends as much as its source. It is
        the local's one binding (its init) when that is all it was ever
        bound to, followed through sole-binding aliases the chain moves
        from (an unpack target's per-element temp is one), so the
        per-element provenance of the direct call carries over; otherwise
        the name itself, which lends every element."""
        t = self.ctx.get_expr_type(name)
        if t is None or not _handle_lends_elements(t):
            return None
        cur = name
        seen: set[str] = set()
        while True:
            decl = self.ctx.func.var_decl_by_name.get(cur.name)
            if (decl is None or decl.init is None
                    or self.ctx.is_reseated(cur.name)):
                return name
            init = decl.init
            # A source still read after the alias shares its handle, so
            # only a moved-from one hands its provenance over.
            if (not isinstance(init, TpyName)
                    or init.name in seen
                    or init not in self.ctx.all_last_uses):
                return init
            seen.add(cur.name)
            cur = init

    @staticmethod
    def member_order(actual: TpyType,
                     ) -> Callable[[tuple[TpyType, ...]], list[TpyType]]:
        """The order a value of type `actual` is tried against a union's
        members (`_union_member_order`), for a caller that walks a slot's
        members itself."""
        return lambda members: _union_member_order(actual, members)

    def _pair(self, expected: TpyType, actual: TpyType) -> tuple[str, str]:
        """`disambiguated_pair` over the user-facing spellings
        (`diag_type`)."""
        return disambiguated_pair(self.diag_type(expected),
                                  self.diag_type(actual))

    def call_type_text(self, t: TpyType) -> str:
        """How an overload diagnostic ("No matching overload", "Ambiguous
        overload") spells an argument's or a candidate parameter's type:
        the user-facing type (`diag_type`), without the ownership and
        reference qualifiers the call machinery carries, an unresolved int
        literal the Python `int`, an empty container that holds nothing yet
        the bare container (`list`)."""
        shown = self.diag_type(unwrap_own(unwrap_ref_type(t)))
        if (isinstance(shown, NominalType) and shown.type_args
                and all(isinstance(a, UnknownElementType)
                        for a in shown.type_args)):
            return shown.name
        return user_type_name(shown)

    def call_signature_text(self, fi: 'FunctionInfo') -> str:
        """A candidate as an overload diagnostic lists it."""
        return (f"{fi.name}("
                f"{', '.join(self.call_type_text(p.type) for p in fi.params)})")

    def diag_type(self, t: TpyType, slot: TpyType | None = None) -> TpyType:
        """User-facing type for diagnostics: a pending container literal is
        unresolved during body analysis, so render the container it
        resolves to instead of the internal Pending* repr, at every depth.
        `slot` is the declared slot the value is stored into: a list
        literal whose element that store decides is named at the slot's
        element."""
        into = self.pend.cell_container(t)
        if into is not None and slot is not None:
            # Born or not: the store into `slot` is what decides (or
            # seeds) the leaves, so the container is named at the slot's.
            container, _, _ = self.pend.meets_list(
                into, slot, "stored", order=self.member_order(t))
            if container is not None:
                root = root_of(into)
                return root.spelled(container_parts(
                    self.pend.context_elem(container), root))

        def elem(e: TpyType) -> TpyType:
            if isinstance(e, IntLiteralType):
                return self.ctx.default_int_for_literal(e)
            if isinstance(e, FloatLiteralType):
                return FLOAT
            if isinstance(e, LiteralType):
                return e.base_type
            if isinstance(e, PendingNumType):
                # An element not decided yet: the type it has so far.
                return self.pend.known_type(e)
            if isinstance(e, TupleType):
                return e.map_inner_types(elem)
            return self.diag_type(e)
        if isinstance(t, PendingListType):
            return make_list(elem(t.element_type))
        if isinstance(t, PendingDictType):
            return make_dict(elem(t.key_type), elem(t.value_type))
        if isinstance(t, PendingSetType):
            return make_set(elem(t.element_type))
        if isinstance(t, PendingNumType):
            # A dict's or set's leaf: the type it has so far.
            return self.pend.known_type(t)
        return t.map_inner_types(self.diag_type)

    def is_auto_move_use(self, expr: 'TpyExpr | None') -> bool:
        """Single authority for "this name read auto-moves": a last-use mark
        on an owned local/param whose storage has no borrower that liveness
        cannot see. Bind-based aliases (b = a, a = o.inner, n = xs[0]) are
        in the prescan alias maps and modeled by the liveness walk itself
        with dead-alias precision; call-result borrows (return_borrows_from)
        and iterator borrows are not, so any such borrower demotes the move
        to the copy path. The mark is retracted on demotion so codegen
        (which reads all_last_uses directly) lands on the same decision.
        """
        if not (isinstance(expr, TpyName)
                and expr in self.ctx.all_last_uses
                and self._is_owned_var(expr.name)):
            return False
        return not self.demoted_by_hidden_borrow(expr)

    def auto_move_copied_elements(self, expr: 'TpyExpr | None'
                                  ) -> frozenset[int] | None:
        """For a name read that auto-moves, the tuple elements an owning sink
        still copies: those the binding holds by reference (a borrowed name
        or a borrowing call's element) are pointers, and the storage lift
        deref-copies them at the last use too. Empty when the move copies
        nothing; None when the read does not auto-move."""
        if not self.is_auto_move_use(expr):
            return None
        return self.ctx.func.bp_copy_into_own_idxs(expr.name)

    def require_movable(self, expr: TpyExpr, context: str) -> None:
        """At a relocation point (the caller has confirmed `expr` auto-moves),
        reject a non-movable source with a clean field-chain diagnostic instead
        of leaving it to surface as a raw C++ deleted-move error deep in the
        generated code. A fresh prvalue never reaches here (it is not a
        last-use name read), so factory returns of a non-movable type stay
        legal -- only relocating a named value is rejected."""
        t = self.ctx.get_expr_type(expr)
        if t is None or t.is_movable():
            return
        chain = why_not_movable(t)
        detail = render_move_chain(chain) if chain is not None else f"'{t}' is not movable"
        raise self.ctx.error(
            f"cannot move into {context}: {detail}\n"
            f"  Define '__move__' on the type to relocate its contents, "
            f"or copy() if it is copyable.",
            expr,
        )

    def demoted_by_hidden_borrow(self, expr: TpyName) -> bool:
        """The borrow-gate primitive: when ``expr``'s storage has a borrower
        the liveness alias maps cannot see, retract the last-use mark (sema
        and codegen both read all_last_uses, so the copy fallback is
        consistent) and return True. Callers check the mark themselves --
        this only decides and applies the demotion.
        """
        known = (self.ctx.func.current_alias_sources.keys()
                 | self.ctx.func.current_chain_alias_sources.keys())
        if self.ctx.func.borrow_tracker.has_borrowers_outside(expr.name, known):
            self.ctx.all_last_uses.discard(expr)
            return True
        return False

    def _warns_copy_into_any(self, inner: TpyType) -> bool:
        """Reference types whose into-Any storage silently copies.

        Caller must pre-unwrap Ref/Readonly/Own. Returns True for non-value
        records, list, dict, set; False for primitives, value-type records,
        str (collapses to std::string), bytes/BytesView, Ptr[T].
        """
        if isinstance(inner, NominalType):
            return not inner.is_value_type() and not inner.is_protocol
        return is_list(inner) or is_dict(inner) or is_set(inner)

    def is_copy_call(self, expr: TpyExpr) -> bool:
        """Check if expression is a copy() or copy_iter() call from the tpy module."""
        if isinstance(expr, TpyCoerce):
            return self.is_copy_call(expr.expr)
        if not isinstance(expr, TpyCall):
            return False
        # Check if this function name maps to tpy.copy or tpy.copy_iter
        # (handles aliases like "from tpy import copy as c")
        if not isinstance(expr.func, TpyName):
            return False
        if expr.func_name in self.ctx.imported_names:
            module_name, func_name = self.ctx.imported_names[expr.func_name]
            return module_name == "tpy" and func_name in ("copy", "copy_iter")
        return False

    def check_own_consumption(self, expr: TpyExpr) -> None:
        """Mark Own[T] param as consumed if expr transfers ownership.

        Handles two patterns:
        - Auto-move: bare name at last use of a movable variable
        - copy(): explicit copy transfers ownership of the param's value
        """
        if isinstance(expr, TpyName):
            if self.is_auto_move_use(expr):
                self.ctx.mark_own_param_consumed(expr.name)
            return
        if self.is_copy_call(expr) and isinstance(expr, TpyCall) and expr.args:
            inner = expr.args[0]
            if isinstance(inner, TpyName):
                self.ctx.mark_own_param_consumed(inner.name)

    def check_own_lvalue_into_own(
        self, own_type: OwnType, expr: TpyExpr, context: str,
        *, action: str = "return", whole_slot: bool = True,
    ) -> bool:
        """Check that a borrowed source feeding an Own[T] slot is movable, an
        explicit copy(), or otherwise safe to consume. True when the slot
        copies the source implicitly (the copy the warning declares).

        Borrowed means aliasing storage that outlives the expression -- an
        lvalue, or a borrow-returning call; the slot's question is not
        whether the source has an address.

        Used by the return-statement Own[T] / per-element Own[tuple[T,...]]
        checks (action="return") and the call-arg per-element check
        (action="pass"). A borrowed source COPIES and warns at both, the
        same text the insert / `Own[T]` parameter / `yield` slots emit: an
        element of a tuple takes the rule its type takes alone.

        Args:
            own_type: The Own[T] slot type.
            expr: The source expression occupying the slot.
            context: Slot description for error messages, e.g. "return type",
                "tuple element 1", "argument 'pname' element 0".
            action: "return" or "pass" -- selects the verb in error messages.
            whole_slot: the source fills the WHOLE `Own[T]` slot. False for
                one element of a returned tuple, whose warning names it.
        """
        if self.is_copy_call(expr):
            self.check_own_consumption(expr)
            return False
        if action == "return" and whole_slot:
            moved = self.returned_owned_element(expr)
            if moved is not None:
                self.ctx.returned_element_moves.add(expr)
                self.ctx.mark_own_element_consumed(*moved)
                return False
        if not self.arrives_borrowed(expr):
            return False
        return self._check_own_borrowed_source(
            own_type, expr, context, action, whole_slot=whole_slot)

    def returned_owned_element(self, expr: TpyExpr) -> 'tuple[str, int] | None':
        """`(name, i)` for a returned `name[i]` that MOVES an owned element
        out of a tuple this body owns: element `i` is `Own[...]` and the
        return is the tuple's last use, so nothing reads the moved-from
        element (the other elements stay where they are). None otherwise."""
        if not (isinstance(expr, TpySubscript) and isinstance(expr.obj, TpyName)):
            return None
        index = expr.index
        if isinstance(index, TpyIntLiteral):
            idx = index.value
        elif (isinstance(index, TpyUnaryOp) and index.op == "-"
              and isinstance(index.operand, TpyIntLiteral)):
            idx = -index.operand.value
        else:
            return None
        tt = self.ctx.get_expr_type(expr.obj)
        tt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt)))
              if tt is not None else None)
        if not isinstance(tt, TupleType):
            return None
        if idx < 0:
            idx += len(tt.element_types)
        if not (0 <= idx < len(tt.element_types)
                and isinstance(unwrap_readonly(tt.element_types[idx]), OwnType)):
            return None
        if not self.is_auto_move_use(expr.obj):
            return None
        return expr.obj.name, idx

    def returned_captured_name(self, expr: TpyExpr) -> 'TpyName | None':
        """The owned NAME a `return` hands over although a closure of the
        body captures it -- the closure may still read it after the return,
        so an owning slot copies it rather than moving. None otherwise, and
        for a return a `finally` defers: that one moves after the finally
        (it is marked a last use)."""
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
        if (isinstance(expr, TpyName)
                and expr.name in self.ctx.func.closure_pinned
                and expr not in self.ctx.all_last_uses
                and self._is_owned_var(expr.name)):
            return expr
        return None

    def _check_own_borrowed_source(
        self, own_type: OwnType, expr: TpyExpr, context: str, action: str,
        *, whole_slot: bool = True,
    ) -> bool:
        """`check_own_lvalue_into_own`'s tail, once the source is known to be
        borrowed: it warns and copies -- unless the source is a value type, a
        movable Own local, or a consuming method's own field. A `@nocopy`
        payload errors, because there is no copy for the warning to
        describe. True when the slot copies."""
        # Value types are always safe -- copied, not aliased. OwnType.is_value_type
        # returns True (Own represents a moved value), so we have to peel Own
        # first to see whether the underlying T is genuinely a value type.
        # A value-shaped type HOLDING an open type parameter (`T | None`,
        # `tuple[T, int32]`) is not: the parameter may be a reference type,
        # which the copy duplicates as it would a bare `T`. (A concrete
        # held instance is the per-element check's.)
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        raw_type = self.ctx.get_raw_expr_type(inner)
        if raw_type is not None:
            unwrapped = unwrap_ref_type(raw_type)
            inner_after_own = unwrapped.wrapped if isinstance(unwrapped, OwnType) else unwrapped
            if (inner_after_own.is_value_type()
                    and not (own_copy.type_param_names(inner_after_own)
                             and own_copy.contains_reference_type(
                                 inner_after_own))):
                return False
            # A WHOLE returned Own-typed name moves out (C++ moves a returned
            # local) unless a closure captures it: that one copies, declared
            # below like any owning slot's. One ELEMENT of a tuple literal is
            # declared below too -- `return (b, b)` hands back two copies
            # where CPython hands back one object twice.
            if action == "return" and isinstance(unwrapped, OwnType):
                if (self.is_auto_move_use(expr) if not whole_slot
                        else self.returned_captured_name(expr) is None):
                    return False
        # A consuming method's field read that sema let move out of `self`.
        if isinstance(inner, TpyFieldAccess) and inner.consuming_move:
            return False
        if self.is_auto_move_use(expr):
            self.require_movable(expr, context)
            self.check_own_consumption(expr)
            return False
        expr_type = self.ctx.get_expr_type(expr)
        # Non-copyable, not just @nocopy: a record with `__del__` has no copy
        # constructor either, so the copy the warning below would declare
        # could not be built.
        is_nocopy = (expr_type is not None
                     and self.ctx.is_type_non_copyable(expr_type))
        if is_nocopy:
            reason = (self.ctx.nocopy_reason(expr_type)
                      if self.ctx.is_type_nocopy(expr_type)
                      else f"non-copyable type '{unwrap_readonly(expr_type)}'")
            is_movable = (isinstance(expr, TpyName)
                          and self._is_owned_var(expr.name))
            if is_movable:
                raise self.ctx.error(
                    f"{reason} is used after this point "
                    f"and cannot be moved into {context} Own[{own_type.wrapped}]. "
                    f"Remove later uses or restructure the code.",
                    expr
                )
            verb = "returned" if action == "return" else "passed"
            tail = (f": {CONSUMING_FIELD_MOVE_NOTE}"
                    if self.is_consuming_field_borrow(expr)
                    else ". Only the original owner can be moved at its last use")
            raise self.ctx.error(
                f"{reason} cannot be {verb} as "
                f"{context} Own[{own_type.wrapped}]{tail}.",
                expr
            )
        # The owning slot copies and says so, exactly as the insert, the
        # `Own[T]` parameter and the `yield` do -- one owning-slot rule, one
        # text, and `copy(...)` silences it everywhere. The copy is an
        # acknowledged CPython divergence (CPython hands over the very
        # object), which is what the warning declares. A tuple element names
        # its element.
        where = "" if whole_slot else f" ({context})"
        if where:
            self.ctx.own_element_copies.add(expr)
        payload = own_type.wrapped
        if self._is_value_type_param(payload):
            # A `T: ValueType` bound proves the copy, so there is nothing
            # to declare -- the same exemption the insert slot applies.
            return True
        value_type = self.diag_type(self.ctx.get_expr_type(expr))
        # Whether this copies at all is the instantiation's answer, not
        # the body's, so an open payload defers to the discharge.
        if not self.ctx.defer_own_copy_verdict(
                value_type, payload, f"owned storage{where}", expr):
            self.ctx.warning(
                f"copies {value_type} into owned storage{where}; "
                f"{self.copy_remedy(expr)}",
                expr
            )
        return True

    def _is_value_type_param(self, typ: TpyType) -> bool:
        """Check if a TypeParamRef has a ValueType bound in the current context."""
        if not isinstance(typ, TypeParamRef):
            return False
        bound = self.type_ops.get_type_param_bound(typ.name)
        return bound is not None and isinstance(bound, NominalType) and bound.qualified_name() == "tpy.ValueType"

    def deep_convertible_to_union(self, actual: TpyType, union: TpyType) -> bool:
        """Whether a concrete list/dict's (possibly nested) leaves would each
        coerce to a member of the recursive-union wrapper `union` -- i.e. the
        container could in principle be element-wise rebuilt into it. Pure
        check, no side effects. Used only to drive a clear rejection diagnostic:
        TPy does NOT implicitly convert (that would be a hidden O(n) deep copy);
        the user must build the value as the alias or pass a literal."""
        members: 'tuple[TpyType, ...] | None' = None
        if isinstance(union, UnionType):
            members = union.members
        elif isinstance(union, RecursiveAliasInstanceType):
            members = union.alternatives()
        if members is None:
            return False
        # A list literal local is still a PendingListType at call-analysis time
        # (list-vs-Array is decided at end-of-function); both resolutions rebuild
        # into the union's list member, so accept either.
        if is_list(actual) or is_array(actual) or isinstance(actual, PendingListType):
            if not any(is_list(m) for m in members):
                return False
            elem = (actual.element_type if isinstance(actual, PendingListType)
                    else actual.get_element_type())
            return self._elem_convertible_to_union(elem, union)
        if is_dict(actual) or isinstance(actual, PendingDictType):
            dict_member = next((m for m in members if is_dict(m) and m.type_args), None)
            if dict_member is None:
                return False
            if isinstance(actual, PendingDictType):
                key_t, val_t = actual.key_type, actual.value_type
            elif actual.type_args:
                key_t, val_t = actual.type_args[0], actual.type_args[1]
            else:
                return False
            # The union's dict member fixes the key type (e.g. str); the value
            # is what gets deep-converted, so only the key must already match.
            if key_t != dict_member.type_args[0]:
                return False
            return self._elem_convertible_to_union(val_t, union)
        return False

    def _elem_convertible_to_union(self, elem: TpyType, union: TpyType) -> bool:
        if (is_list(elem) or is_dict(elem) or is_array(elem)
                or isinstance(elem, (PendingListType, PendingDictType))):
            return self.deep_convertible_to_union(elem, union)
        # A rebuild is only NEEDED when the leaf is not already the union: a
        # literal materialized against the union hint already has union-typed
        # elements (no conversion, handled by the literal path), whereas a
        # concrete container variable (e.g. dict[str, int32]) has a genuinely
        # different element. The leaf must also coerce to a union member.
        # `_check_compat` with no source_expr has no side effects.
        if self._resolve_recursive_refs(elem) == union:
            return False
        return not isinstance(
            self._check_compat(elem, union, "deep-union leaf"), CompatError)

    def _check_union_member_type_args(
        self, actual: NominalType, expected: NominalType,
        context: str, loc: object,
        source_expr: 'TpyExpr | None',
        is_return: bool, coercion_ctx: CoercionContext,
    ) -> bool:
        """Check Container[Lit] -> Container[Expr] union-member coercion.

        Returns True if all differing type args have expected = recursive union
        and actual is a valid member of that union.
        """
        for a_arg, e_arg in zip(actual.type_args, expected.type_args):
            if a_arg == e_arg:
                continue
            if not isinstance(a_arg, TpyType) or not isinstance(e_arg, TpyType):
                return False
            # Resolve recursive-alias placeholders (AliasRef from parser, or
            # forward NominalType refs from cross-module aliases) into their
            # underlying UnionType so the union-coercion path below applies.
            e_resolved = (self._resolve_recursive_refs(e_arg)
                          if isinstance(e_arg, (NominalType, AliasRef)) else e_arg)
            if isinstance(e_resolved, UnionType) and e_resolved.needs_wrapper():
                # Container type-arg slot is storage form (the container stores
                # values, no address-take fires for pointer-repr Optional).
                result = self._check_compat(
                    a_arg, e_resolved, context, loc, None, is_return, coercion_ctx,
                    target_is_storage_form=True, sink_owns=True,
                )
                if isinstance(result, CompatError):
                    return False
            else:
                return False
        # Rewrite expression type so codegen emits correct template args
        # (e.g. Box<Expr> not Box<Lit>)
        if source_expr is not None:
            self.ctx.set_expr_type(source_expr, expected)
            # Also rewrite call_type on constructor calls (codegen uses this
            # for template args: Box<Expr> vs Box<Lit>)
            if isinstance(source_expr, TpyCall) and source_expr.call_type is not None:
                source_expr.call_type = expected
        return True

    def is_covariant_generic_upcast(self, actual: TpyType, expected: TpyType) -> bool:
        """True if `actual -> expected` is a covariant-generic wrapper upcast
        (e.g. `Box[Child] -> Box[Parent]`): same user-record generic, differing
        type args, every differing position covariant and a valid C++ upcast
        target. This is a representation-preserving converting move, NOT
        slicing. Shared by `check_type_compatible` and the container-literal
        element guards so the covariant-vs-slice line is drawn in one place."""
        if not (isinstance(actual, NominalType) and actual.is_user_record
                and isinstance(expected, NominalType) and expected.is_user_record
                and actual.name == expected.name
                and actual.type_args and expected.type_args
                and actual.type_args != expected.type_args):
            return False
        record_info = self.ctx.registry.get_record(actual.name)
        if record_info is None or not record_info.type_params:
            return False
        covariant = get_covariant_params(record_info)
        return bool(covariant and self._check_covariant_args(
            record_info, covariant, actual, expected))

    def _check_covariant_args(
        self, record_info: 'RecordInfo', covariant: set[str],
        actual: NominalType, expected: NominalType
    ) -> bool:
        """Check if all type args are compatible under covariance rules."""
        for i, param_name in enumerate(record_info.type_params):
            if i >= len(actual.type_args) or i >= len(expected.type_args):
                return False
            actual_arg = actual.type_args[i]
            expected_arg = expected.type_args[i]
            if actual_arg == expected_arg:
                continue
            if param_name not in covariant:
                return False  # invariant position must match exactly
            if not isinstance(actual_arg, TpyType) or not isinstance(expected_arg, TpyType):
                return False
            # Target must be a @dynamic protocol or class parent for C++ pointer upcast
            if not self._is_covariant_target(actual_arg, expected_arg):
                return False
        return True

    def _is_representational_subtype(self, child: TpyType, parent: TpyType) -> bool:
        """True if a `Ptr[child] -> Ptr[parent]` C++ pointer upcast is valid,
        where `child` is a bounded type parameter.

        Matches `child`'s IMMEDIATE bound against the actual `parent` at each
        hop -- never flattens a bound to bound(parent), which would wrongly
        admit a sibling subtype (`U: T`, `T: Pet` does NOT make `U` any
        `Pet`). A `U: V`, `V: U` cycle is guarded by a seen-set.

        A protocol `parent` is declined: a structural conformer satisfies the
        bound without inheriting, so the plain C++ pointer upcast would be
        invalid. Those need the adapter path (out of scope here). Only a class
        or a (further) type-param bound proves the upcast. The concrete
        instantiation's soundness is enforced at the call site.
        """
        if is_protocol_type(parent):
            return False
        # Record the originating type-param as representational so codegen at
        # call sites knows to materialize structural conformers via Adapter.
        origin_param = child.name if isinstance(child, TypeParamRef) else None
        seen: set[str] = set()
        cur = child
        while isinstance(cur, TypeParamRef) and cur.name not in seen:
            seen.add(cur.name)
            bound = self.type_ops.get_type_param_bound(cur.name)
            if bound is None:
                return False
            if bound == parent:
                if origin_param is not None:
                    self.ctx.func.current_representational_params.add(origin_param)
                return True
            if isinstance(bound, NominalType) and bound.is_user_record:
                if self._is_covariant_target(bound, parent):
                    if origin_param is not None:
                        self.ctx.func.current_representational_params.add(origin_param)
                    return True
                return False
            if isinstance(bound, TypeParamRef):
                cur = bound
                continue
            return False
        return False

    def _callable_signature_satisfies(
        self, actual_params: tuple[TpyType, ...], actual_return: TpyType,
        expected: CallableType, loc: 'SourceLocation | None',
    ) -> bool:
        """Whether a concrete callable signature satisfies an expected
        Fn/Callable contract. Function params are CONTRAVARIANT: the callee
        must accept everything the contract may pass, so each expected param
        must be compatible with the callee's declared param (e.g. a callee
        taking int32 | None satisfies Fn[[int32], ...], not the reverse).
        Returns are covariant; a void contract accepts any return.
        """
        if len(actual_params) != len(expected.param_types):
            return False
        if not all(self._check_compat(e, a, "param", loc) is None
                   for a, e in zip(actual_params, expected.param_types)):
            return False
        return (self._check_compat(actual_return, expected.return_type,
                                   "return", loc) is None
                or isinstance(expected.return_type, VoidType))

    def _is_covariant_target(self, child: TpyType, parent: TpyType) -> bool:
        """Check if child -> parent is valid for covariant conversion.

        Requires C++ struct inheritance: @dynamic protocol implementation
        or class inheritance.
        """
        if not isinstance(child, NominalType) or not isinstance(parent, NominalType):
            return False
        # @dynamic protocol: child implements parent
        if is_protocol_type(parent):
            proto_info = protocol_info_of(parent)
            if proto_info and proto_info.is_dynamic:
                if self.protocols and self.protocols.type_conforms_to_protocol(child, parent):
                    return True
            return False
        # Class inheritance
        if child.is_user_record and parent.is_user_record:
            return self.ctx.registry.is_subclass_of(child, parent)
        return False

    def is_local_shadow(self, name: str) -> bool:
        """Check if a name is bound in a local scope, shadowing a global.

        NarrowingTracker._is_rebindable_global (narrowing.py) walks the same
        chain with a depth-based terminator and a param exclusion -- keep the
        two in sync when changing shadow semantics."""
        scope = self.ctx.func.current_scope
        while scope and scope is not self.ctx.global_scope:
            if name in scope.bindings:
                return True
            scope = scope.parent
        return False

    def _is_owned_var(self, name: str) -> bool:
        """Check if a variable has owned storage (eligible for auto-move).

        Uses OwnType in scope as the primary authority.  Falls back to
        rvalue_vars for variables excluded from OwnType wrapping
        (reassigned vars, move-through vars).
        """
        # A consuming method (`self: Own[Self]`) owns its receiver: `self` is
        # this frame's value, so its last use relocates it exactly as an
        # `Own[T]` param does. Same ownership fact the `self.field` arm in
        # `_check_own_borrowed_source` reads, one level up -- `self` is not in
        # `func.params`, so the loop below never sees it.
        if name == "self" and self.ctx.in_consuming_method:
            return True
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            # Own[T] params, and owned-element tuple params (the ownership-
            # transfer `std::tuple<...>&&` ABI -- movable like a scalar Own[T]).
            for pname, ptype in func.params:
                if pname == name:
                    return func.takes_ownership_of(pname, ptype)
            # Locals: check scope type.
            # Exclude for-loop vars: they get Own[T] from consuming iteration
            # element types but are not "rvalue-initialized" in the same sense
            # as constructor calls. Their movability is handled by the
            # consuming iteration system (consumed_loop_vars).
            scope_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
            if scope_type and isinstance(scope_type, OwnType) and name not in self.ctx.func.loop_vars:
                return True
            # Fallback: rvalue_vars covers move-through vars and
            # reassigned-but-all-rvalue vars not wrapped with OwnType.
            # Hoisted vars are not movable (T* pointer-locals). Require
            # owned_locals too: a borrow-producing init (reference-returning
            # call, ternary/and-or of reference lvalues) lands in rvalue_vars
            # for hoist eligibility but is a `T&` alias -- moving out of it
            # would corrupt the aliased source.
            if (name in self.ctx.func.rvalue_vars
                    and name in self.ctx.func.owned_locals
                    and name not in self.ctx.func.hoisted_vars):
                if name in self.ctx.func.current_reassigned_vars:
                    return name not in self.ctx.func.current_lvalue_reassigned
                return True
            return False
        # Top-level: non-value-type vars become pointer-globals, can't be moved
        var_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
        if var_type:
            inner = var_type.wrapped if isinstance(var_type, OwnType) else var_type
            if not inner.is_value_type():
                return False
        return True

    def _stored_copy_return_message(self, expr: TpyExpr,
                                    bare_src: 'TpyType | None',
                                    return_type: TpyType) -> str | None:
        """The dangling-return error for an inferred str/bytes local that
        owns only because the view rule copies the field or element it was
        bound to (`y = h.s; return y` under `-> StrView`); None for any other
        local. Its hint names the spellings that keep a view -- for `str`
        only: a `BytesView` of a field does not lower yet in either spelling."""
        if not (isinstance(expr, TpyName) and isinstance(bare_src, PendingViewType)
                and is_str_view_type(return_type)):
            return None
        family = _view_return_family(return_type)
        entries = self.ctx.func.view_ids_by_name.get(expr.name, ())
        if family is None or len(entries) != 1:
            return None
        fam, var_id = entries[0]
        info = self.ctx.view_vars(fam).get(var_id)
        if (info is None or not info.owns_stored_source
                or info.used_in_augassign or info.passed_to_promote_param
                or info.reassigned_from_owned or info.source_mutated
                or info.source_var_ids):
            return None
        display = family[0]
        return (f"Cannot return {display} referencing a local or temporary; "
                + _view_keeping_hint(expr.name, display, info.stored_source))

    def _view_constructor_arg(self, expr: TpyExpr) -> TpyExpr | None:
        """If expr is a borrowing-view constructor call, return the borrowed-from arg.

        StrView / BytesView / Span[T] / SpanIter[T] all borrow from their
        first argument, so provenance/dangling predicates should recurse
        into that argument rather than treating the constructor call as
        an opaque value.
        """
        if not isinstance(expr, TpyCall) or not expr.args:
            return None
        if expr.call_type is not None and is_borrowing_view_type(expr.call_type):
            return expr.args[0]
        return None

    def _name_is_param_or_global(self, name: str) -> bool:
        """True if the name refers to caller-owned storage that outlives the call.

        Used by is_safe_to_return_expr and is_dangling_return to keep the
        self/param/non-shadowed-global checks in one place. is_param_derived_expr
        intentionally diverges by omitting the self check (self is the receiver,
        not a "parameter" for borrow-contract purposes).

        An `Own[T]` param is CALLEE-owned and dies at function exit, so it does
        NOT confer return-safety on borrows rooted in it; the same holds for
        `self` in a consuming method. (Returning the owned value itself moves
        by value through the OwnType return path, which never reaches the
        dangling checks.) Generic `Own[...T...]` params keep the legacy
        params-are-safe treatment: the body is analyzed pre-instantiation
        where value-ness (by-value vs by-reference return) is unknown --
        same TypeParamRef exemption the per-element tuple dangle check uses.
        """
        func = self.ctx.func.current_function
        # Only a real receiver is durable by being `self`; in a free function
        # or a staticmethod the name is an ordinary local, param or global and
        # must answer from its storage, like any other name.
        if name == "self" and self.ctx.self_names_receiver():
            return not (isinstance(func, TpyFunction) and func.is_consuming)
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    own_inner = unwrap_optional_own(unwrap_readonly(unwrap_send_sync(ptype)))
                    return (own_inner is None
                            or contains_type_param(own_inner))
        return self._name_is_module_global(name)

    def _name_is_module_global(self, name: str) -> bool:
        """Whether `name` reads the module slot here: a global binding that no
        parameter and no local shadows.

        The global arm of `_name_is_param_or_global`, which calls it, so a
        caller that needs to know WHY that predicate said durable asks the
        same question it did. (`is_local_shadow` and
        `NarrowingTracker._is_rebindable_global` walk the same chain.)
        """
        if name in self.ctx.func.current_param_names:
            return False
        return (name in self.ctx.global_scope.bindings
                and not self.is_local_shadow(name))

    def _borrow_root_names(self, expr: TpyExpr, out: set[str]) -> None:
        """Collect the names whose storage a borrow-form return points into.

        Walks the same chain `is_param_derived_expr` certifies as durable, so
        every root that predicate answers for is a root reported here; a local
        bound from one of them resolves through the borrow tracker.
        """
        if isinstance(expr, TpyCoerce):
            self._borrow_root_names(expr.expr, out)
            return
        if isinstance(expr, TpyNamedExpr):
            self._borrow_root_names(expr.value, out)
            return
        if isinstance(expr, TpyName):
            out.add(expr.name)
            bt = self.ctx.func.borrow_tracker
            for src in bt.storage_roots_or_self(expr.name):
                out.add(_storage_root(src))
            return
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            self._borrow_root_names(expr.obj, out)
            return
        if isinstance(expr, TpyIfExpr):
            self._borrow_root_names(expr.then_expr, out)
            self._borrow_root_names(expr.else_expr, out)
            return
        if (isinstance(expr, TpyCall) and expr.call_type is not None
                and expr.call_type.is_pointer() and expr.args):
            self._borrow_root_names(expr.args[0], out)
            return
        view_arg = self._view_constructor_arg(expr)
        if view_arg is not None:
            self._borrow_root_names(view_arg, out)
            return
        for src in self._call_return_lend_sources(expr):
            self._borrow_root_names(src.expr, out)

    @staticmethod
    def _call_return_lend_sources(expr: TpyExpr) -> 'list[LendSource]':
        """The operands a call's result points into by its recorded
        `return_borrows_from` (`call_lend_sources`); [] for any other
        expression."""
        if not isinstance(expr, (TpyCall, TpyMethodCall)):
            return []
        ops = call_borrow_operands(expr)
        if ops is None:
            return []
        recorded = recorded_return_borrow_sources(ops.fi)
        return (call_lend_sources(ops, recorded, expr_type=None)
                if recorded else [])

    def _return_borrows_global_storage(self, name: str,
                                       return_type: TpyType) -> str | None:
        """How a borrow-form return rooted in global `name` points INTO that
        global's slot -- `'reference'`, `'view'`, or None when it copies a
        self-contained value out instead.

        Two ways it points in: the global's own type is a reference type, so
        the return renders `T&` / `T*` at the slot; or a VIEW is formed over
        the slot's buffer (`str` -> `StrView`). A global that already holds a
        handle -- a `Ptr[T]`, a `Span[T]` over someone else's buffer -- hands
        back a COPY of that handle, and reseating the slot leaves the copy
        (and what it points at) untouched.

        `return_type` must be the BORROW-form type the caller is checking; a
        value-typed one copies out of the slot and never reaches here (the
        answer would be the reference-typed global's `'reference'` regardless,
        which is why the callers filter first).
        """
        declared = self.ctx.global_scope.lookup(name)
        if declared is None:
            return None
        bare = unwrap_readonly(unwrap_ref_type(declared))
        if not bare.is_value_type():
            return 'reference'
        if is_borrowing_view_type(return_type) and not is_borrowing_view_type(bare):
            return 'view'
        return None

    def rebound_global_borrow_root(
            self, expr: TpyExpr,
            return_type: TpyType) -> tuple[str, str, str] | None:
        """The (global, rebinding function, leg) a returned borrow roots in,
        when some function in this module rebinds that global -- else None.
        `leg` is `_return_borrows_global_storage`'s verdict, which decides
        which remedy the diagnostic can offer.

        A module global is durable storage, so the dangling checks pass a
        borrow of one: it outlives every call. `global S; S = ...` breaks
        that, because it RESEATS the slot and frees the buffer the borrow
        points into, and the rebind may live in a different function than
        the borrow -- which is why the fact is module-wide
        (`SemanticContext.rebound_globals`) rather than re-derived here.
        """
        if not self.ctx.rebound_globals:
            return None
        roots: set[str] = set()
        self._borrow_root_names(expr, roots)
        for name in sorted(roots):
            owner = self.ctx.rebound_globals.get(name)
            if owner is None or not self._name_is_module_global(name):
                continue
            leg = self._return_borrows_global_storage(name, return_type)
            if leg is not None:
                return name, owner, leg
        return None

    def is_param_derived_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression's root storage derives from parameters or globals."""
        if isinstance(expr, TpyCoerce):
            return self.is_param_derived_expr(expr.expr)
        # String / bytes literals live in rodata; None literal is a nullptr.
        # All three have non-local, permanent storage -- safe to borrow from.
        if isinstance(expr, (TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
            return True
        if isinstance(expr, TpyName):
            # Parameters are param-derived
            func = self.ctx.func.current_function
            if isinstance(func, TpyFunction):
                for pname, _ptype in func.params:
                    if pname == expr.name:
                        return True
            # Globals are param-derived (live forever), but only if
            # the name isn't shadowed by a local binding
            if expr.name in self.ctx.global_scope.bindings:
                if not self.is_local_shadow(expr.name):
                    return True
            # Variables tracked as param-derived
            if self.ctx.func.bp_is_param_derived(expr.name):
                return True
            return False
        if isinstance(expr, TpyFieldAccess):
            return self.is_param_derived_expr(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.is_param_derived_expr(expr.obj)
        if isinstance(expr, TpyIfExpr):
            return (self.is_param_derived_expr(expr.then_expr)
                    and self.is_param_derived_expr(expr.else_expr))
        # Pointer constructors derive provenance from their argument (address-taking)
        if isinstance(expr, TpyCall) and expr.call_type is not None and expr.call_type.is_pointer() and expr.args:
            return self.is_param_derived_expr(expr.args[0])
        view_arg = self._view_constructor_arg(expr)
        if view_arg is not None:
            return self.is_param_derived_expr(view_arg)
        # Function/method calls with return_borrows_from: result is param-derived if
        # the borrowed-from argument(s) are themselves param-derived.
        # No recorded source (unanalyzed, or a new value) leaves the loop body
        # unrun and falls through to return False below -- correct either way.
        if any(self.is_param_derived_expr(src.expr)
               for src in self._call_return_lend_sources(expr)):
            return True
        # Constructors, function calls, literals -- local storage
        return False

    def is_safe_to_return_expr(self, expr: TpyExpr) -> bool:
        """Whether binding a local to this RHS makes the local safe to return.

        Recurses through composite forms so a per-call-site OR doesn't miss
        ``sv = p if cond else pick(q)`` (neither arm uniformly param-derived
        nor uniformly a trusted call). Must be a superset of
        is_param_derived_expr to maintain the subset invariant on which
        flow-merge intersection relies.
        """
        if isinstance(expr, TpyCoerce):
            return self.is_safe_to_return_expr(expr.expr)
        # Walrus safety follows the wrapped value (superset invariant with
        # is_dangling_return's TpyNamedExpr case).
        if isinstance(expr, TpyNamedExpr):
            return self.is_safe_to_return_expr(expr.value)
        # String / bytes / None literals live in rodata / are nullptr --
        # permanent storage, safe to return. Mirrors is_param_derived_expr.
        if isinstance(expr, (TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
            return True
        if isinstance(expr, TpyName):
            return (self._name_is_param_or_global(expr.name)
                    or self.ctx.func.bp_is_safe_to_return(expr.name))
        if isinstance(expr, TpyFieldAccess):
            return self.is_safe_to_return_expr(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.is_safe_to_return_expr(expr.obj)
        if isinstance(expr, TpyIfExpr):
            return (self.is_safe_to_return_expr(expr.then_expr)
                    and self.is_safe_to_return_expr(expr.else_expr))
        # Pointer constructors derive safety from their argument (address-taking).
        if isinstance(expr, TpyCall) and expr.call_type is not None and expr.call_type.is_pointer() and expr.args:
            return self.is_safe_to_return_expr(expr.args[0])
        view_arg = self._view_constructor_arg(expr)
        if view_arg is not None:
            return self.is_safe_to_return_expr(view_arg)
        # Calls with return_borrows_from are not handled explicitly here:
        # is_dangling_return already recurses into the borrowed args, so a
        # call whose return borrows from a param-derived (or otherwise safe)
        # arg is non-dangling and lands in this branch as safe. If
        # is_dangling_return is ever tightened for return_borrows_from, mirror
        # the explicit arm from is_param_derived_expr to preserve the
        # superset invariant.
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            return not self.is_dangling_return(expr)
        return False

    def _call_borrow_operands_dangle(
            self, expr: 'TpyCall | TpyMethodCall', fi: Any, *,
            gen_yield: bool, assume_unknown_calls_safe: bool) -> bool:
        """True if an operand the callee's return borrows from dangles -- an
        owned element of a tuple-literal operand lives in the tuple
        temporary, so it dangles as a scalar temporary argument does.

        Index -1 is the receiver (the 8b convention). An index naming no
        operand leaves the result's provenance unknown: only the closed-world
        reading calls that dangling -- the open-world one keeps the historical
        skip, where a missing index is not evidence against the callee.
        """
        obj = expr.obj if isinstance(expr, TpyMethodCall) else None
        recorded = recorded_return_borrow_sources(fi)
        ops = CallOperands(fi, obj, list(expr.args), expr.kwargs or {})
        present = {idx for idx, _ in ops.positioned()}
        if obj is not None:
            present.add(-1)
        if not assume_unknown_calls_safe and not recorded <= present:
            return True
        return any(
            src.temp_backed or self.is_dangling_return(
                src.expr, gen_yield=gen_yield,
                assume_unknown_calls_safe=assume_unknown_calls_safe)
            for src in call_lend_sources(ops, recorded, expr_type=None,
                                         temp_backing=True))

    def is_mutable_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is a mutable lvalue (can get a mutable Ptr).

        This is like is_lvalue but also rejects read-only sources like Span elements.
        """
        if isinstance(expr, TpyCoerce):
            return self.is_mutable_lvalue(expr.expr)
        # Named variables: mutable unless the binding's type is `readonly[T]`
        # (parameter declared as `readonly[T]`, or `self` inside a @readonly
        # method). Without this check, a readonly binding could launder
        # away its const via `&x` / `take_ptr(x)` / record-to-Ptr upcasts.
        if isinstance(expr, TpyName):
            expr_type = self.ctx.get_expr_type(expr)
            if isinstance(expr_type, ReadonlyType):
                return False
            return True
        # Field access on a mutable lvalue is also mutable
        if isinstance(expr, TpyFieldAccess):
            return self.is_mutable_lvalue(expr.obj)
        # Subscript: check if the base is a read-only type (Span[readonly[T]], str)
        if isinstance(expr, TpySubscript):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if is_span(obj_type) and is_readonly_span(obj_type):
                return False  # Span[readonly[T]] elements are read-only
            if is_any_str_type(obj_type):
                return False
            return self.is_mutable_lvalue(expr.obj)
        return False

    def _is_frame_resident_local(self, name: str) -> bool:
        """True if `name` is a generator/coro frame-resident local -- hoisted
        into `func.generator_locals`, so its storage is a lifetime-stable
        `tpy::frame_slot<T>`. Only meaningful once generator_locals is populated
        (post-body); the borrow-yield rooting check is deferred until then.
        """
        fn = self.ctx.func.current_function
        if not isinstance(fn, TpyFunction) or not fn.generator_locals:
            return False
        return any(n == name for n, _ in fn.generator_locals)

    def _local_has_borrowable_storage(self, expr: TpyName, *,
                                      view_source: bool = False) -> bool:
        """True if a frame-resident local's own storage is a reference object
        that can be handed out by borrow. A value-typed local (array, tuple of
        values, primitive) has no such storage -- yielding it as a reference
        type is a representation-changing copy, i.e. a fresh temporary.

        `view_source`: the local is the BACKING STORAGE of a yielded view, not
        the yielded value. An owning str/bytes local is value-typed but holds a
        buffer, and the frame field owns that buffer for the frame's whole
        lifetime -- the view points into it, nothing is copied.
        """
        t = self.ctx.get_expr_type(expr)
        if t is None:
            return False
        t = unwrap_readonly(t)
        if not t.is_value_type():
            return True
        if not view_source:
            return False
        storage = self.ctx.view_storage_verdict(t) or t
        return view_family_for_type(storage) is not None

    def is_dangling_return(self, expr: TpyExpr, *, view_source: bool = False,
                           gen_yield: bool = False,
                           assume_unknown_calls_safe: bool = True) -> bool:
        """Check if returning this expression would create a dangling reference.

        `view_source`: the expression is the backing storage a returned *view*
        borrows from (StrView/BytesView/Span/...), not the returned value
        itself. A local that is `safe_to_return` (movable owned local returned
        by value) is NOT a safe view source -- the view aliases storage that is
        moved/destroyed at the return -- so that exemption is skipped.

        `gen_yield`: the expression is the value of a generator/coro `yield`.
        A frame-resident local (in `func.generator_locals`) is then a valid
        borrow root -- its `tpy::frame_slot<T>` storage is stable for the
        generator's lifetime, and the consumer-side ephemeral-borrow rule
        forbids retaining the yielded borrow past the next resume. The
        exemption fires only at the genuine root leaf (a frame-local name, or
        a field/subscript chain bottoming out in one), never via a name inside
        a call -- calls reach their own provenance branches above, not the
        TpyName leaf.

        `assume_unknown_calls_safe`: the DIAGNOSTIC reading (the default)
        trusts a callee it cannot see through -- the callee is responsible for
        not handing back a dangling reference, and rejecting every opaque call
        would reject valid code. The generic yield-slot VERDICT reads the same
        walk closed-world instead (False): an unproven source must fall to the
        value slot rather than lend something the callee may have
        materialized -- an erased `Fn` result carries no ownership at all (see
        `docs/CALLABLE_CONTRACT_DESIGN.md`), and a callee whose body is not
        analyzed yet has no fact to read. Closed-world, a call is non-dangling
        only where its resolved signature borrows from operands that are
        themselves non-dangling; those operands are then walked with
        `gen_yield` in force, because a verdict follows provenance the whole
        way down rather than stopping at the conservative call boundary.
        """
        strict = not assume_unknown_calls_safe
        if isinstance(expr, TpyCoerce):
            return self.is_dangling_return(
                expr.expr, view_source=view_source, gen_yield=gen_yield,
                assume_unknown_calls_safe=assume_unknown_calls_safe)
        # A walrus hands out its value: `return (t := items[0])` returns the
        # subscript read, so provenance follows the wrapped expression.
        if isinstance(expr, TpyNamedExpr):
            return self.is_dangling_return(
                expr.value, view_source=view_source, gen_yield=gen_yield,
                assume_unknown_calls_safe=assume_unknown_calls_safe)
        # Array/dict literal - creates temporary
        if isinstance(expr, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            return True

        # f-string always materializes a fresh std::string temporary, so a view
        # of it (`return f"..."` as StrView, or `f"..."[a:b]`) dangles.
        if isinstance(expr, TpyFString):
            return True

        # List repeat - creates temporary
        if isinstance(expr, TpyListRepeat):
            return True

        # An awaited result: an owned result is a fresh temporary
        # materialized in the frame (dangling as a borrow). A
        # borrow-returning await hands back a pointer whose roots are the
        # awaited call's receiver / borrowed args (the inner coroutine's
        # own return check gated its sources to its params/self/fields,
        # and those bind exactly the call's operands) -- recurse into
        # them like the sync call/method arms, so a local-rooted receiver
        # is rejected while a param-rooted one chains. Missing borrow
        # facts and untraceable operands (a bound handle name) fail
        # closed.
        if isinstance(expr, TpyAwait):
            if not expr.await_result_is_borrow:
                return True
            op = expr.value
            if isinstance(op, (TpyCall, TpyMethodCall)):
                fi = op.resolved_function_info
                obj = op.obj if isinstance(op, TpyMethodCall) else None
                if fi is not None and recorded_return_borrow_sources(fi):
                    return self._call_borrow_operands_dangle(
                        op, fi, gen_yield=gen_yield and strict,
                        assume_unknown_calls_safe=assume_unknown_calls_safe)
                if strict:
                    return True
                roots = ([obj] if obj is not None else []) + list(op.args)
                return any(self.is_dangling_return(r) for r in roots)
            return True

        # Constructor call - creates temporary
        if isinstance(expr, TpyCall):
            # A borrow-declared call sema stamped a fresh value: its reference
            # may point into a temporary operand or an iterator's step.
            if expr.result_form.is_fresh:
                return True
            # Pointer constructors: dangling depends on the argument, not the pointer itself
            if isinstance(expr.call_type, PtrType):
                if not expr.args:
                    # A null pointer has no referent, so nothing it points at
                    # can outlive anything -- it is a VALUE, not a borrow, and
                    # the closed-world reading agrees: `Ptr[T]` is a value type,
                    # so the generic yield slot stores this copy by value
                    # (`val_or_ref<T>` at a value `T`) rather than lending it.
                    return False
                return self.is_dangling_return(
                    expr.args[0],
                    assume_unknown_calls_safe=assume_unknown_calls_safe)

            view_arg = self._view_constructor_arg(expr)
            if view_arg is not None:
                return self.is_dangling_return(
                    view_arg,
                    assume_unknown_calls_safe=assume_unknown_calls_safe)

            # @value_ptr_coercion functions (e.g. take_ptr): result borrows
            # from the arg value, so dangling depends on the arg.
            fi = expr.resolved_function_info
            if fi is not None and fi.value_ptr_coercion and expr.args:
                return self.is_dangling_return(
                    expr.args[0],
                    assume_unknown_calls_safe=assume_unknown_calls_safe)

            # Generic type constructor creates a temporary
            if expr.call_type is not None:
                return True

            # Expression callees (e.g. a call through a returned callable) yield a
            # temporary we cannot prove outlives the return -- treat as dangling.
            if not isinstance(expr.func, TpyName):
                return True

            # Record constructor
            if expr.func_name in self.ctx.registry.records:
                return True

            # Function returning Own[T] creates a temporary (by-value return).
            # Only check user-defined functions (builtins don't appear in
            # registry.functions).
            if self.ctx.registry.get_function(expr.func_name) is not None:
                fi = expr.resolved_function_info
                if fi and isinstance(fi.return_type, OwnType):
                    return True

            # Function whose return borrows from args (e.g. generators storing
            # non-value params as T& references): dangles if any borrowed arg dangles
            if fi is not None and recorded_return_borrow_sources(fi):
                if self._call_borrow_operands_dangle(
                        expr, fi, gen_yield=gen_yield and strict,
                        assume_unknown_calls_safe=assume_unknown_calls_safe):
                    return True
            elif strict:
                # Closed-world: no borrow fact means no proven provenance --
                # an opaque stub, an erased callee, or a body not yet analyzed.
                return True

            # A function returning owned str/String/bytes creates a temporary
            # that dangles if returned as a view (StrView/BytesView).
            if fi is not None and (is_str_type(fi.return_type) or is_string_type(fi.return_type)
                                   or is_bytes_type(fi.return_type)):
                return True

            # bytearray is the lone builtin REFERENCE type whose constructor is
            # non-generic, so it misses both the generic-constructor path
            # (`call_type`, which catches list/dict/set) and the registry path
            # (user records) -- and its constructor resolves to a void
            # `__init__`, so the fi.return_type line above can't see it either.
            # Keyed on the call's result type, this catches both the constructor
            # and any function/method returning a fresh bytearray: returning that
            # temporary by bare reference dangles (use Own[bytearray]).
            if is_bytearray_type(unwrap_own(unwrap_readonly(
                    self.ctx.get_expr_type(expr)))):
                return True

            # Regular function call - assume it returns something safe
            # (the callee is responsible for not returning dangling refs)
            return False

        # Locals dangle unless their root or current binding is safe.
        if isinstance(expr, TpyName):
            if self._name_is_param_or_global(expr.name):
                return False
            # A movable owned local is safe to return BY VALUE, but a view
            # borrowing from it still dangles (the storage is moved/destroyed
            # at the return) -- so don't honor that exemption for a view source.
            if not view_source and self.ctx.func.bp_is_safe_to_return(expr.name):
                return False
            # A yielded frame-resident local roots in stable frame-slot storage
            # that outlives the suspension (see gen_yield in the docstring).
            # Gated on the local having storage to lend: a reference-typed local
            # always does; a value-typed one only as the owning BUFFER of a
            # yielded view (str/bytes), since yielding the value itself as a
            # reference is a representation-changing copy -- a fresh temporary.
            if (gen_yield and self._is_frame_resident_local(expr.name)
                    and self._local_has_borrowable_storage(
                        expr, view_source=view_source)):
                return False
            return True

        # Field access - safe only if the object itself is safe.
        if isinstance(expr, TpyFieldAccess):
            return self.is_dangling_return(
                expr.obj, view_source=view_source, gen_yield=gen_yield,
                assume_unknown_calls_safe=assume_unknown_calls_safe)

        # Subscript - safe only if the container itself is safe. A user-record
        # `__getitem__` is not routed through the method-call arm below: the
        # node carries a resolved fi only for a pointer-repr Optional return
        # (`_tag_record_getitem`), and that shape borrows its receiver, so the
        # receiver recursion already gives the call arm's answer. An owned
        # `__getitem__` return behind a view carries no fi to consult and
        # escapes this walk (BUGS.md#record-getitem-owned-return-view-dangle).
        if isinstance(expr, TpySubscript):
            return self.is_dangling_return(
                expr.obj, view_source=view_source, gen_yield=gen_yield,
                assume_unknown_calls_safe=assume_unknown_calls_safe)

        # Method call returning owned str/String creates a temporary
        # std::string that dangles if returned as StrView.
        if isinstance(expr, TpyMethodCall):
            # A borrow-declared call sema stamped a fresh value: its reference
            # may point into a temporary operand.
            if expr.result_form.is_fresh:
                return True
            fi = expr.resolved_function_info
            if fi is not None and (is_str_type(fi.return_type) or is_string_type(fi.return_type)
                                   or is_bytes_type(fi.return_type) or is_bytearray_type(fi.return_type)):
                return True
            # A user method returning Own[T] creates a by-value temporary;
            # taking its address (the Optional pointer-repr return path) would
            # emit `&(obj.method())`. Builtin container methods (list.pop, ...)
            # also return Own[T] but are exempt -- mirrors the free-function
            # branch's user-function-only OwnType guard above.
            if fi is not None and isinstance(fi.return_type, OwnType):
                obj_type = self.ctx.get_expr_type(expr.obj)
                if isinstance(obj_type, NominalType) and obj_type.is_user_record:
                    return True
            # Method whose return borrows from its receiver/args: dangles if
            # the borrowed source dangles -- mirrors the free-function branch;
            # index -1 is the receiver (the 8b convention).
            if fi is not None and recorded_return_borrow_sources(fi):
                if self._call_borrow_operands_dangle(
                        expr, fi, gen_yield=gen_yield and strict,
                        assume_unknown_calls_safe=assume_unknown_calls_safe):
                    return True
            elif strict:
                return True
            return False

        # Ternary - dangles if either branch dangles
        if isinstance(expr, TpyIfExpr):
            return (self.is_dangling_return(
                        expr.then_expr, view_source=view_source,
                        gen_yield=gen_yield,
                        assume_unknown_calls_safe=assume_unknown_calls_safe)
                    or self.is_dangling_return(
                        expr.else_expr, view_source=view_source,
                        gen_yield=gen_yield,
                        assume_unknown_calls_safe=assume_unknown_calls_safe))

        # Unary/Binary ops - might create temporaries, be conservative
        if isinstance(expr, (TpyUnaryOp, TpyBinOp)):
            return True

        # Fail closed: an unrecognized expression kind that produces a borrowing
        # view (str/bytes view, span) is of unknown provenance, so treat it as
        # dangling rather than silently safe -- otherwise the next new node kind
        # recreates the view-lifetime hole under fresh syntax. Recognized kinds
        # with defined provenance (names, calls, methods, subscripts, ...) return
        # above; only genuinely-unhandled view-typed exprs reach here.
        expr_type = self.ctx.get_expr_type(expr)
        if expr_type is not None and is_borrowing_view_type(unwrap_readonly(expr_type)):
            return True

        # Default: assume safe -- but closed-world, an expression form with no
        # provenance arm above has nothing proving it outlives the frame.
        return strict

    def _call_result_is_fresh(self, expr: TpyExpr, fi: Any) -> bool:
        """The call-shaped `expr` (callee `fi`) PROVABLY hands back a fresh
        owned value -- a constructor, an `Own[T]` return, or a reference-type
        result the callee's convention returns by value. A callable value
        counts only through a declared `Own[T]`: its signature is the `Fn`
        type's, which says nothing else about ownership."""
        if fi.is_constructor or (
                isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
                and expr.func_name in self.ctx.registry.records):
            return True
        if (isinstance(expr, TpyCallLike)
                and expr.result_form is not ResultForm.NOT_DECLARED):
            return expr.result_form is not ResultForm.BORROW
        ret = unwrap_readonly(fi.return_type) if fi.return_type else None
        if isinstance(ret, OwnType):
            return True
        if (fi.is_callable_value or ret is None
                or is_open_type_param_return(fi.root.return_type)):
            return False
        return (isinstance(ret, NominalType) and not ret.is_value_type()
                and not (is_protocol_type(ret) or is_dyn_protocol(ret))
                and not call_returns_cpp_ref(self.ctx, fi))

    def borrow_temp_root(self, expr: TpyExpr) -> TpyExpr | None:
        """The fresh owned temporary a borrow-form `expr` PROVABLY points
        into, or None when no root is proven one.

        Follows the borrow chain the way the call-lending facts record it:
        a field or container read borrows its object, a call borrows the
        operands its `return_borrows_from` names (`call_lend_sources`), and
        a conditional may hand out either arm. A callee whose borrow facts
        are unknown proves nothing, and neither does a name -- whatever it
        is bound to lives outside the expression."""
        if isinstance(expr, TpyCoerce):
            return self.borrow_temp_root(expr.expr)
        if isinstance(expr, TpyFieldAccess):
            if expr.hidden_call is not None:
                return None
            return self.borrow_temp_root(expr.obj)
        if isinstance(expr, TpySubscript):
            if (expr.slice_function_info is not None
                    or expr.getitem_function_info is not None):
                return None
            return self.borrow_temp_root(expr.obj)
        if isinstance(expr, TpyIfExpr):
            return (self.borrow_temp_root(expr.then_expr)
                    or self.borrow_temp_root(expr.else_expr))
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            return (self.borrow_temp_root(expr.left)
                    or self.borrow_temp_root(expr.right))
        # A generator expression is a handle: what it yields borrows the
        # storage it iterates, not the frame.
        if (isinstance(expr, CONTAINER_LITERAL_NODES)
                and not isinstance(expr, TpyGeneratorExpression)):
            return expr
        if isinstance(expr, TpyCall) and expr.call_type is not None:
            if (isinstance(expr.call_type, PtrType)
                    or is_borrowing_view_type(expr.call_type)):
                return None
            return expr
        if isinstance(expr, TpyCallLike) and expr.result_form.is_fresh:
            return expr
        ops = call_borrow_operands(expr)
        if ops is None:
            return None
        if self._call_result_is_fresh(expr, ops.fi):
            return expr
        if ops.fi.root.return_borrows_from is None:
            return None
        for src in call_lend_sources(
                ops, recorded_return_borrow_sources(ops.fi),
                expr_type=None, temp_backing=True):
            if src.temp_backed:
                return src.expr
            root = self.borrow_temp_root(src.expr)
            if root is not None:
                return root
        return None

    def check_view_return_dangle(self, expr: TpyExpr, return_type: TpyType,
                                 loc: SourceLocation | None,
                                 *, for_yield: bool = False) -> None:
        """Variant of check_dangling_reference for value-return contexts
        (lambda bodies, yield values). The full check_dangling_reference
        rejects local/temporary returns when the return type is a non-value
        object -- a false positive for value-return contexts where the C++
        callable/generator machinery moves/copies by value. Only the
        view-dangling and pointer-dangling sub-rules apply here.
        """
        if isinstance(return_type, PtrType) or is_borrowing_view_type(return_type):
            # Pass the source's analyzed type (as the main return path does) so a
            # view-typed local with validated provenance (a param-/literal-
            # derived StrView/BytesView) follows that provenance instead of
            # being rejected as owned storage behind a view.
            self.check_dangling_reference(
                expr, return_type, loc,
                source_type=self.ctx.get_expr_type(expr),
                for_yield=for_yield)

    def _check_rebound_global_borrow(self, expr: TpyExpr,
                                     return_type: TpyType, verb: str) -> None:
        """Reject a borrow return/yield rooted in a module global that some
        function rebinds.

        The dangling checks pass this shape and are right to: a module global
        outlives every call. What they cannot see is that `global S; S = ...`
        REBINDS the slot, freeing the buffer the borrow points into -- so the
        caller reads storage that was released between the call and the read.

        Called from the arms of `check_dangling_reference` that borrow-check a
        (sub-expression, type) pair, with that pair -- never off a parallel
        classification of `return_type`, which drifted from the arms and
        missed the tuple one.
        """
        found = self.rebound_global_borrow_root(expr, return_type)
        if found is None:
            return
        name, owner, leg = found
        family = _view_return_family(return_type)
        if leg == 'view' and family is not None:
            fix = (f"{verb.capitalize()} {family[1]} to hand back an owned "
                   f"copy, or stop rebinding '{name}'.")
        else:
            # The reference-typed leg has no owning escape to offer, and the
            # remedy must not claim the rebind itself is illegal: a plain `=`
            # rebind is rejected in its own right (which of the two errors the
            # user sees depends only on which function sema reaches first),
            # but `+=` is an in-place extend in CPython that the lowering does
            # not admit yet, so naming the function is all that is true.
            fix = f"Stop rebinding '{name}' in '{owner}'."
        raise self.ctx.error(
            f"Cannot {verb} a borrow of module variable '{name}': function "
            f"'{owner}' rebinds '{name}', and the rebind frees the storage "
            f"this borrow points into. {fix}",
            expr
        )

    def drain_deferred_escape_checks(self) -> None:
        """Run the return/yield rooting checks deferred during body analysis.

        Both lists are drained from this one place, at the END of a body and
        after `func.generator_locals` is populated, because both answers need
        the post-body state: a yielded frame-resident local is a valid borrow
        root only once the frame-local set exists, and a str/bytes local's
        storage is what the deduction settled. Yield-rooting runs first -- its
        checks can themselves defer a view-storage entry.

        Nothing is dropped: the view-storage pass is final, so a check that
        deferred during analysis either passes or raises here.
        """
        roots = self.ctx.func.pending_yield_root_checks
        self.ctx.func.pending_yield_root_checks = []
        for value, elem_type, loc in roots:
            self.check_dangling_reference(value, elem_type, loc, for_yield=True)

        checks = self.ctx.func.pending_view_storage_checks
        self.ctx.func.pending_view_storage_checks = []
        for expr, return_type, loc, source_type, for_yield in checks:
            self.check_dangling_reference(
                expr, return_type, loc, source_type=source_type,
                for_yield=for_yield, view_storage_final=True)

    def check_dangling_reference(self, expr: TpyExpr, return_type: TpyType,
                                 loc: SourceLocation | None,
                                 source_type: TpyType | None = None,
                                 *, for_yield: bool = False,
                                 view_storage_final: bool = False) -> None:
        """Check if returning (or yielding) expr as a reference would dangle.

        Reference types are returned/yielded by reference. A local variable or
        newly constructed object would create a dangling reference. `source_type`
        is the analyzed type of `expr` before return-coercion; it lets the
        recursive-union-wrapper case tell a wrap-into-wrapper (fresh temporary)
        from a reference to an existing wrapper value.

        `for_yield`: the same rooting rule applies to a generator's borrow yield
        (`Iterator[T]`, T non-value) -- the generator frame survives suspension,
        so a yield must root in frame-held storage exactly as a return must root
        in caller-outliving storage. Only the diagnostic wording differs: the
        owning escape is `Iterator[Own[T]]` rather than `Own[T]`.
        """
        verb = "yield" if for_yield else "return"

        def _own_fix(t: object, *, cap: bool) -> str:
            s = (f"declare the generator 'Iterator[Own[{t}]]' to yield by value"
                 if for_yield else f"use Own[{t}] to return by value")
            return (s[0].upper() + s[1:]) if cap else s

        # Only check object types (value types are returned by value)
        # OwnType returns by value (ownership transfer), so no dangling risk
        # Pointer types need dangling checks (the pointer value may point to a local)
        if isinstance(return_type, PtrType):
            self._check_rebound_global_borrow(expr, return_type, verb)
            if self.is_dangling_return(expr, gen_yield=for_yield):
                raise self.ctx.error(
                    f"Cannot {verb} pointer to local or temporary value; "
                    f"the {verb}ed pointer would dangle",
                    expr
                )
            return
        # Value types that hold an interior pointer -- returning one that
        # borrows from a local would dangle after the function returns.
        view_msg = _dangling_view_message(return_type)
        if view_msg is not None:
            self._check_rebound_global_borrow(expr, return_type, verb)
            inner = expr.expr if isinstance(expr, TpyCoerce) else expr
            view_arg = self._view_constructor_arg(inner)
            if view_arg is not None:
                # A view constructor / slice: its argument is the backing
                # storage, so a movable owned local there is still a dangling
                # view source.
                if self.is_dangling_return(view_arg, view_source=True,
                                           gen_yield=for_yield):
                    raise self.ctx.error(view_msg, inner)
            else:
                # Returning a local directly. If the local is ITSELF a view
                # (its provenance was validated at binding, e.g. a literal- or
                # param-derived BytesView), follow that provenance. If it is
                # owned storage coerced into a view (a `bytearray`/`list` local
                # returned as BytesView/Span), the storage dies at the return,
                # so use the strict view-source check.
                # A str/bytes local whose storage the deduction has not settled
                # cannot answer this yet -- its type says "undecided", and a
                # USE must not be what decides the storage. Wait for the
                # verdict and re-run against it: a VIEW then follows the
                # binding provenance this branch consults, an OWNED local is
                # dead-on-return storage rejected exactly as an annotated
                # `str`/`bytes` local is.
                bare_src = (unwrap_readonly(source_type)
                            if source_type is not None else None)
                decided = self.ctx.view_storage_verdict(bare_src)
                if (decided is not None and not view_storage_final
                        and not self.ctx.view_storage_settled(bare_src)):
                    self.ctx.func.pending_view_storage_checks.append(
                        (expr, return_type, loc, source_type, for_yield))
                    return
                effective_src = decided if decided is not None else source_type
                src_is_view = (effective_src is not None
                               and _dangling_view_message(effective_src) is not None)
                if self.is_dangling_return(expr, view_source=not src_is_view,
                                           gen_yield=for_yield):
                    raise self.ctx.error(
                        (None if for_yield else self._stored_copy_return_message(
                            expr, bare_src, return_type))
                        or view_msg, expr)
            return
        # A tuple's borrow form (std::tuple<..., T*, ...>) stores each non-value
        # member by pointer. The check sees through a readonly wrap (the slot is
        # still borrow form). A borrow member nested inside an *inner* tuple is
        # rejected outright -- codegen's tuple borrow/storage conversion is flat
        # (doesn't recurse), so such a slot is miscompiled regardless of whether
        # the source dangles (BUGS.md). A top-level borrow member is fine when
        # rooted, so it gets the per-element fresh-source dangling check.
        tuple_rt = unwrap_readonly(return_type)
        if isinstance(tuple_rt, TupleType):
            nested_bad = self._nested_tuple_borrow_member(tuple_rt)
            if nested_bad is not None:
                raise self.ctx.error(
                    f"A tuple {verb} with a non-value member ('{nested_bad}') nested "
                    f"inside another tuple is not yet supported -- the nested borrow "
                    f"slot is miscompiled. Flatten the tuple, or make the member "
                    f"Own[{nested_bad}].",
                    expr
                )
            self._check_tuple_elem_dangle(tuple_rt, expr, verb, source_type,
                                          gen_yield=for_yield)
            self._check_tuple_member_local(tuple_rt, expr, verb)
            if not for_yield:
                self._check_tuple_storage_return_root(tuple_rt, expr)
            return
        if (return_type.is_value_type()
                or isinstance(return_type, (VoidType, OwnType))):
            return

        # Every arm below returns a bare reference form (`T&` / `T*`), so one
        # rebound-global check covers the wrapper, Optional and reference arms.
        self._check_rebound_global_borrow(expr, return_type, verb)

        # Recursive-union wrappers follow the reference-type convention: a bare
        # `X` return lowers to `X&`. Coercing a value / list / None into the
        # wrapper materializes a fresh wrapper temporary, so returning it by
        # reference would dangle -- only an existing wrapper-typed reference
        # (parameter / field / global / a ref-returning call) is safe. Require
        # Own[X] for fresh values, matching the list / dict / record rule.
        if unwrap_readonly(return_type).needs_wrapper():
            src = expr.expr if isinstance(expr, TpyCoerce) else expr
            # The value is wrapped into the wrapper (a fresh temporary) unless
            # its source type is already that wrapper -- i.e. a reference to
            # existing wrapper data. The wrap is implicit at codegen, so the
            # pre-coercion source_type is the only reliable signal.
            from_existing_wrapper = (source_type is not None
                and unwrap_readonly(unwrap_ref_type(source_type)).needs_wrapper())
            if not from_existing_wrapper or self.is_dangling_return(
                    src, gen_yield=for_yield):
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as reference. "
                    f"Reference type '{return_type}' is {verb}ed by reference. "
                    f"{_own_fix(return_type, cap=True)}.",
                    expr
                )
            return

        # Optional[T] for non-value T returns T* -- returning a local would dangle.
        # But `return None` is always safe (returns nullptr).
        if isinstance(return_type, OptionalType):
            if isinstance(expr, TpyNoneLiteral):
                return
            if self.is_dangling_return(expr, gen_yield=for_yield):
                call = expr.expr if isinstance(expr, TpyCoerce) else expr
                if (isinstance(call, TpyCallLike)
                        and call.result_form is ResultForm.COPY
                        and own_copy.type_has_type_param(return_type)):
                    # The hedged generic copy of an open Optional: the
                    # owning `Own[V | None]` return has no storage slot for
                    # an open payload yet, so suggesting it would fail too.
                    raise self.ctx.error(
                        f"Cannot {verb} the result of {call.call_display} as "
                        f"'{return_type}': in a generic body it is a copy, "
                        f"since its payload may be a class, and returning "
                        f"that copy is not supported yet; bind it to a local "
                        f"and read it there",
                        expr
                    )
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as '{return_type}'. "
                    f"The {verb}ed pointer would dangle. "
                    f"{verb.capitalize()} a reference to parameter data, or {_own_fix(return_type, cap=False)}.",
                    expr
                )
            return

        # Check if the expression is safe to return as a reference
        if self.is_dangling_return(expr, gen_yield=for_yield):
            if is_protocol_type(return_type):
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as '{return_type}'. "
                    f"Dynamic protocol return requires a value that outlives the caller "
                    f"(parameter or global).",
                    expr
                )
            fn = self.ctx.func.current_function
            if for_yield and isinstance(fn, TpyFunction) and fn.is_genexpr:
                # A genexpr has no return annotation to put `Own[...]` in.
                raise self.ctx.error(
                    f"Cannot yield a freshly-constructed '{return_type}' from a "
                    f"generator expression: it is handed out by reference and would "
                    f"dangle. Use a list comprehension '[...]' to materialize owned "
                    f"elements instead.",
                    expr
                )
            call = expr.expr if isinstance(expr, TpyCoerce) else expr
            if (isinstance(call, TpyCallLike)
                    and call.result_form is ResultForm.COPY
                    and isinstance(unwrap_readonly(return_type), TypeParamRef)):
                # The hedged generic copy: the call is not a temporary in
                # the source, so say why its result is one here.
                raise self.ctx.error(
                    f"Cannot {verb} the result of {call.call_display} by "
                    f"reference: in a generic body it is a copy, since "
                    f"'{return_type}' may be a class. "
                    f"{_own_fix(return_type, cap=True)}.",
                    expr
                )
            raise self.ctx.error(
                f"Cannot {verb} local or temporary as reference. "
                f"Reference type '{return_type}' is {verb}ed by reference. "
                f"{_own_fix(return_type, cap=True)}.",
                expr
            )

    def _tuple_literal_fresh_borrow_elem(
            self, tuple_type: TupleType, inner: TpyExpr) -> int | None:
        """First element index whose non-value (borrow-form) slot has a fresh /
        dangling source in this tuple literal, else None.

        Drives the owns-fresh-tuple-member assignment flag (see
        `update_tuple_member_local_facts`). The literal yield/return path has its
        own per-element loop (`_check_tuple_elem_dangle`), which is strictly
        stronger: it also catches a value coerced into a wrapper element via the
        pre-coercion source type, which this name-based flag cannot see.
        """
        if not isinstance(inner, TpyTupleLiteral):
            return None
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(inner.elements):
                break
            # Recursive-union wrappers are borrow form here too, so a fresh
            # wrapper member dangles like any other borrow element; the
            # is_dangling_return source check tells a fresh local from a
            # param/self-rooted wrapper, so there is no needs_wrapper() skip.
            if (not et.is_value_type() and not isinstance(et, (OwnType, TypeParamRef))
                    and self.is_dangling_return(inner.elements[i])):
                return i
        return None

    def _tuple_elem_still_borrow(self, tuple_type: TupleType, idx: int) -> bool:
        """Whether element `idx` of the *declared* boundary tuple is still
        borrow form. The hazard flag's index comes from the assignment-inferred
        type; the boundary type may differ (an `Own[T]` element -- the escape --
        is moved by value and safe), so re-check before rejecting. A
        recursive-union wrapper element is borrow form (`X&`), so a flagged fresh
        wrapper member is NOT exempt (the durable check never flags a wrapper)."""
        if idx >= len(tuple_type.element_types):
            return False
        et = tuple_type.element_types[idx]
        return not (et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)))

    def _check_tuple_elem_dangle(self, tuple_type: TupleType, expr: TpyExpr,
                                 verb: str, source_type: TpyType | None = None,
                                 *, gen_yield: bool = False) -> None:
        """Per-element dangling check for a top-level tuple return/yield literal.

        Each non-value member is stored by pointer in the tuple's borrow form,
        so a freshly-constructed member would dangle. The owning fix is
        element-scoped (`Own[T]` on the member), not the whole return/iterator.
        Nested-tuple members are handled upstream by the unsupported-shape check.
        A recursive-union-wrapper member is also borrow form: a value / list /
        None coerced into it is a fresh temporary, so the per-element check uses
        the pre-coercion `source_type` member types to tell a wrap-into-wrapper
        leaf from a reference to an existing wrapper.

        This is also where the rebound-global rule sees a tuple: the TUPLE is a
        value type, so only its members are borrows, and each member is checked
        against its own element type (a `StrView` member is a view over the
        slot even though `StrView` itself is a value type).
        """
        inner = peel_value_wrappers(expr)
        # A ternary returns whichever arm is taken -- check both.
        if isinstance(inner, TpyIfExpr):
            self._check_tuple_elem_dangle(tuple_type, inner.then_expr, verb,
                                          source_type, gen_yield=gen_yield)
            self._check_tuple_elem_dangle(tuple_type, inner.else_expr, verb,
                                          source_type, gen_yield=gen_yield)
            return
        if not isinstance(inner, TpyTupleLiteral):
            return
        src_elems = (source_type.element_types
                     if isinstance(source_type, TupleType)
                     and len(source_type.element_types) == len(tuple_type.element_types)
                     else None)
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(inner.elements):
                break
            if _elem_is_borrow_form(et):
                self._check_rebound_global_borrow(inner.elements[i], et, verb)
            if et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)):
                continue
            bad = self.is_dangling_return(inner.elements[i], gen_yield=gen_yield)
            if not bad and et.needs_wrapper():
                src_et = src_elems[i] if src_elems is not None else None
                from_existing_wrapper = (src_et is not None
                    and unwrap_readonly(unwrap_ref_type(src_et)).needs_wrapper())
                bad = not from_existing_wrapper
            if bad:
                raise self.ctx.error(
                    f"Cannot {verb} local or temporary as tuple element {i}. "
                    f"Type '{et}' is {verb}ed by reference. "
                    f"Use Own[{et}] for this tuple element to {verb} by value.",
                    inner.elements[i]
                )

    def update_tuple_member_local_facts(
            self, name: str, var_type: TpyType | None,
            init_expr: TpyExpr | None) -> None:
        """Set/clear the owns-fresh tuple-member hazard fact for a (re)assigned
        local (see `sema.context`).

        A tuple local bound from a literal with a FRESHLY-constructed non-value
        member is unsafe to yield/return by NAME -- the member is a dying local
        even in pointer borrow form. The hazard is equally present when the
        unsafe local is reached through an alias (`u = t`) or a ternary of
        aliases/literals, so the fact is derived from the init expression's
        provenance, not just a literal at the binding. Recorded here (not
        rejected) so a pure local read stays valid -- only a later yield/return
        of the bare name is rejected, at the boundary. A DURABLE reference
        member needs no fact: the bound local is pointer borrow form and
        aliases the member like CPython.

        Derive before clearing so a self-assignment (`t = t`) re-installs its own
        fact instead of losing it to the pop.
        """
        fresh = None
        owning = False
        copy_into_own: list[int] = []
        borrow_roots: frozenset[str] = frozenset()
        if init_expr is not None and var_type is not None:
            tt = unwrap_readonly(var_type)
            if isinstance(tt, TupleType):
                fresh = self._derive_tuple_member_hazards(tt, init_expr)
                if tt.has_pointer_repr_element():
                    owning = self._derive_owning_storage(init_expr)
                    borrow_roots = self._derive_tuple_borrow_sources(
                        name, tt, init_expr)
                copy_into_own = self._derive_copy_into_own_hazards(init_expr)
        # All four tuple-member hazard fields are (re)derived together and
        # replaced atomically; the rebind clears the name's old facts first
        # (so `t = t` re-installs its own, derived above). owns-fresh maps to
        # the first dangerous element index; the per-element reject/warn sets
        # and the owning flag are flow-sensitive (UNION-merged) so a hazard on
        # any reaching path is caught at the boundary check.
        self.ctx.func.bp_set_tuple_member(
            name,
            owns_fresh_idx=fresh,
            owning_storage=owning,
            copy_into_own_idxs=frozenset(copy_into_own),
            borrow_source_roots=borrow_roots,
        )

    def _derive_tuple_borrow_sources(self, name: str, tt: 'TupleType',
                                     init_expr: TpyExpr) -> frozenset[str]:
        """The storage roots a borrow-form tuple local's element pointers
        alias, so the mark functions can trace a later yield/return of the
        bare name back to the aliased params. Roots are expanded through
        already-recorded tuple locals at record time (values stay terminal,
        so mark-time lookup is single-level and cycle-free); a
        self-assignment keeps the old sources via the derive-before-clear
        order above. Stored on BindingProvenance (UNION-merged at branch
        joins), so branch-divergent binds accumulate both arms' sources.
        """
        sources: set[str] = set()
        for root, _grants_write in tuple_borrow_escape_roots(
                init_expr, tt, False, expr_type=None):
            expanded = self.ctx.func.bp_borrow_source_roots(root)
            for s in (expanded if expanded else (root,)):
                if s != name:
                    sources.add(s)
        return frozenset(sources)

    @staticmethod
    def _is_owning_tuple_call(expr: TpyExpr) -> bool:
        """Whether `expr` is a call returning OWNING tuple storage --
        `Own[tuple[...]]` or a tuple with per-element `Own` slots. A local
        bound from one owns its element storage (the call's return ABI is
        the storage form), unlike a borrow-form tuple call result whose
        pointers the callee already proved durable.
        """
        inner = peel_value_wrappers(expr)
        if not isinstance(inner, (TpyCall, TpyMethodCall)):
            return False
        fi = inner.resolved_function_info
        if fi is None:
            return False
        rt = unwrap_readonly(fi.return_type)
        if isinstance(rt, OwnType):
            return isinstance(unwrap_readonly(rt.wrapped), TupleType)
        return (isinstance(rt, TupleType)
                and any(isinstance(et, OwnType) for et in rt.element_types))

    @classmethod
    def _borrow_carrying_tuple_return(
            cls, expr: TpyExpr) -> 'TupleType | None':
        """The returned tuple type of `expr` when it delivers a tuple with at
        least one BORROWED element -- all-borrow (`tuple[A, B]`) or mixed
        (`tuple[Own[A], B]`) -- else None.

        Such a result is an rvalue, so the whole-tuple lvalue tests skip it, but
        its borrowed elements point at storage the caller still owns: an owning
        sink materializes them, copying what CPython aliases. Distinct from
        `_is_owning_tuple_call`, which asks whether the call owns element
        storage -- a mixed return is BOTH (it owns one half and borrows the
        other), so neither predicate answers for the other.

        A ternary composes like the codegen sibling `renders_own_borrow_tuple`:
        C++ evaluates one arm, so the result carries a borrow only when BOTH do.
        Without this the copy still fired (codegen does recurse) while the
        warning did not, which is a silent copy.
        """
        inner = peel_value_wrappers(expr)
        if isinstance(inner, TpyIfExpr):
            then_t = cls._borrow_carrying_tuple_return(inner.then_expr)
            else_t = cls._borrow_carrying_tuple_return(inner.else_expr)
            return then_t if (then_t is not None and else_t is not None) else None
        if not isinstance(inner, (TpyCall, TpyMethodCall)):
            return None
        fi = inner.resolved_function_info
        if fi is None:
            return None
        rt = unwrap_readonly(fi.return_type)
        if isinstance(rt, TupleType) and rt.has_ref_elements():
            return rt
        return None

    @classmethod
    def _tuple_call_carries_borrow(cls, expr: TpyExpr) -> bool:
        return cls._borrow_carrying_tuple_return(expr) is not None

    @classmethod
    def _borrow_carrying_call_elements(
            cls, expr: TpyExpr | None) -> 'tuple[TpyType, ...] | None':
        """The element types of a borrow-carrying tuple result, or None when
        `expr` is not one. Carries the per-element ownership markers the
        destination slot may have stripped."""
        if expr is None:
            return None
        rt = cls._borrow_carrying_tuple_return(expr)
        return rt.element_types if rt is not None else None

    def warn_pointer_repr_tuple_copy(self, source_expr: TpyExpr | None,
                                     tuple_type: TpyType, dest: str,
                                     loc_node, elem_path: str = "", *,
                                     only: frozenset[int] | None = None) -> bool:
        """Copy diagnostic for a whole value-tuple lvalue source with pointer-repr
        (reference) members stored into owned storage: each such member is
        deep-copied where CPython aliases. Warn per member (error for @nocopy);
        returns whether anything fired. Shared by the subscript/field assignment
        path and the `T -> Own[T]` coercion branch (container literals,
        append/insert/add). Each caller applies its own source gating before
        calling (the coercion/literal callers gate on is_lvalue and not-moved;
        the assignment caller warns for any non-literal/non-copy/non-owning-call
        source); this helper only applies the tuple-specific exemptions -- a
        fresh tuple LITERAL source (whose per-member copy is handled by
        `check_tuple_literal_members` -- a literal needs per-member-expr
        gating, not the per-element-type rule here), an explicit `copy()`, and
        an owning-tuple-call rvalue. A nested value-tuple element is walked
        into (the storage lift copies its reference members just the same);
        `elem_path` carries the outer indices so the message names the member
        as `1.0` rather than restarting at `0`. `only` restricts the
        top-level elements considered to those the source holds by
        reference."""
        if not (isinstance(tuple_type, TupleType)
                and tuple_type.has_nested_pointer_repr_element()):
            return False
        if source_expr is not None:
            if isinstance(peel_value_wrappers(source_expr), TpyTupleLiteral):
                return False
            if self.is_copy_call(source_expr):
                return False
            # An owning call is exempt only when it owns EVERY non-value element
            # -- then the sink copies nothing that aliases the caller. A mixed
            # return also passes _is_owning_tuple_call, and exempting it is what
            # silenced the borrowed half's copy.
            if (self._is_owning_tuple_call(source_expr)
                    and not self._tuple_call_carries_borrow(source_expr)):
                return False
        # Which elements ARRIVE borrowed is a fact about the source, and the
        # target may have had the markers stripped (an annotated
        # `list[tuple[Box, Box]]` slot fed by a `tuple[Own[Box], Box]` call
        # spells element 0 as a plain `Box`). Reading the verdict off the target
        # there would warn about the owned element, which moves and never
        # copies -- so prefer the source's element forms when we have them.
        src_elems = self._borrow_carrying_call_elements(source_expr)
        fired = False
        for i, et in enumerate(tuple_type.element_types):
            if only is not None and i not in only:
                continue
            probe = src_elems[i] if src_elems is not None and i < len(src_elems) else et
            path = f"{elem_path}{i}"
            if not TupleType._element_is_pointer_repr(probe):
                nested = unwrap_own(unwrap_readonly(unwrap_ref_type(probe)))
                if isinstance(nested, TupleType):
                    # The source's own shape gated the whole store above; the
                    # nested walk is a pure type question, so no source expr.
                    fired |= self.warn_pointer_repr_tuple_copy(
                        None, nested, dest, loc_node, f"{path}.")
                continue
            if self.ctx.is_type_non_copyable(et):
                raise self.ctx.error(
                    f"cannot copy non-copyable type '{et}' into {dest} "
                    f"(tuple element {path}){NOCOPY_REMEDIATION_HINT}", loc_node)
            self.ctx.warning(
                f"copies {self.diag_type(et)} into {dest} (tuple element "
                f"{path}); use copy() to make this explicit", loc_node)
            fired = True
        return fired

    def check_tuple_literal_members(self, literal: TpyTupleLiteral,
                                    tuple_type: TpyType, sink: TupleSink,
                                    dest: str, elem_path: str = "", *,
                                    pname: str = "") -> bool:
        """Per-member copy check for a tuple LITERAL at `sink`: member i takes
        the rule a scalar of its type takes at that slot (`tuple-equals-
        scalar`). An `Own`-marked member at a return or an argument takes that
        slot's `Own` rule (a borrowed source copies and warns); at a yield or a
        storage sink it is owned storage, like every unmarked reference member
        at a field or container sink and every member of a nested value tuple.
        A literal's members are separate expressions, so each is gated on its
        own source shape. `pname` names the parameter at an ARG sink. Returns
        whether a warning fired."""
        if not isinstance(tuple_type, TupleType):
            return False
        fired = False
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(literal.elements):
                continue
            m = literal.elements[i]
            path = f"{elem_path}{i}"
            owned = isinstance(et, OwnType)
            if owned and sink is TupleSink.RETURN:
                self.check_own_lvalue_into_own(
                    et, m, f"tuple element {path}", action="return",
                    whole_slot=False)
                continue
            if owned and sink is TupleSink.ARG:
                self.check_own_lvalue_into_own(
                    et, m, f"argument '{pname}' tuple element {path}",
                    action="pass", whole_slot=False)
                continue
            member_t = unwrap_own(et)
            nested = unwrap_readonly(unwrap_ref_type(member_t))
            if isinstance(nested, TupleType):
                fired |= self._check_nested_tuple_member(
                    m, nested, sink, dest, f"{path}.", pname)
                continue
            if member_t.is_value_type() or isinstance(nested, TypeParamRef):
                continue
            if (not owned and sink in _BORROW_FORM_SINKS
                    and not (sink is TupleSink.LOCAL
                             and _declared_call_member(m))):
                continue
            fired |= self._warn_literal_member_copy(
                m, member_t, dest, path,
                last_use_moves=sink in _LAST_USE_MOVES_SINKS)
        return fired

    def _check_nested_tuple_member(self, m: TpyExpr, nested: TupleType,
                                   sink: TupleSink, dest: str, path: str,
                                   pname: str) -> bool:
        """A nested value-tuple member is owned storage wherever the outer
        tuple stores it. A return and a yield reject an unmarked reference
        member nested a level down outright, so there only its `Own`-marked
        members still need their rule."""
        if sink in (TupleSink.RETURN, TupleSink.YIELD):
            peeled = peel_value_wrappers(m)
            if isinstance(peeled, TpyTupleLiteral):
                return self.check_tuple_literal_members(
                    peeled, nested, sink, dest, path, pname=pname)
            return False
        return self.warn_storage_tuple_copy(m, nested, dest, path)

    def _warn_literal_member_copy(self, m: TpyExpr, member_t: TpyType,
                                  dest: str, path: str, *,
                                  last_use_moves: bool) -> bool:
        """One literal member landing in owned storage. Only a member that
        arrives borrowed is copied where CPython aliases; a fresh rvalue
        constructs in place. A copyable member at its last use is suppressed
        (the source is dead, so the copy is unobservable); a `@nocopy` one is
        rejected even then unless the sink moves it (`last_use_moves`), or it
        would reach a deleted copy constructor."""
        if self.is_copy_call(m):
            return False
        if not (self.arrives_borrowed(m) or self._ternary_member_copies(m)):
            self.check_unobserved_call_copy(
                m, member_t, f"{dest} (tuple element {path})")
            return False
        auto_moved = self._is_auto_moved(m)
        if auto_moved and last_use_moves:
            return False
        if self.ctx.is_type_non_copyable(member_t):
            raise self.ctx.error(
                f"cannot copy non-copyable type '{member_t}' into {dest} "
                f"(tuple element {path}){self.nocopy_hint(m)}", m)
        if auto_moved:
            return False
        self.ctx.warning(
            f"copies {self.diag_type(member_t)} into {dest} (tuple element "
            f"{path}); {self.copy_remedy(m)}", m)
        return True

    def check_unobserved_call_copy(self, expr: TpyExpr, slot_t: TpyType,
                                   dest: str) -> None:
        """An owning slot holds a COPY of a borrow-declared call's fresh
        result even where nothing else reaches the object
        (`[max(N(1), N(2), key=f)]` copies out of a temporary operand): no
        warning, since the copy is unobservable, but a non-copyable payload
        has no copy to make."""
        inner = peel_value_wrappers(expr)
        if not (isinstance(inner, TpyCallLike)
                and inner.result_form.copies_operand
                and not inner.copy_observable):
            return
        payload = unwrap_readonly(unwrap_ref_type(unwrap_own(slot_t)))
        if self.ctx.is_type_non_copyable(payload):
            raise self.ctx.error(
                f"cannot copy non-copyable type '{payload}' into {dest}"
                f"{self.nocopy_hint(expr)}", expr)

    def is_consuming_field_borrow(self, expr: 'TpyExpr | None') -> bool:
        """A consuming method's `self.<field>` read that borrows because it
        is not in a `return`: the generic copy remedies (auto-move at last
        use) never apply to it. A generator or async method never moves a
        field, and a read already inside a `return` cannot be moved into
        one, so the `return` remedy is not offered there."""
        fn = self.ctx.func.current_function
        inner = peel_value_wrappers(expr) if expr is not None else None
        return (self.ctx.in_consuming_method
                and not self.ctx.func.in_return_value
                and isinstance(fn, TpyFunction)
                and not fn.is_generator and not fn.is_async
                and isinstance(inner, TpyFieldAccess)
                and isinstance(inner.obj, TpyName) and inner.obj.name == "self"
                and not inner.consuming_move)

    def copy_remedy(self, expr: 'TpyExpr | None') -> str:
        """The remedy clause of an owning slot's copy warning for `expr`."""
        clause = (CONSUMING_FIELD_COPY_CLAUSE
                  if self.is_consuming_field_borrow(expr) else "")
        return f"use copy() to make this explicit{clause}"

    def nocopy_hint(self, expr: 'TpyExpr | None') -> str:
        """The parenthesized remedy of a non-copyable copy error for `expr`."""
        if self.is_consuming_field_borrow(expr):
            return f" ({CONSUMING_FIELD_MOVE_NOTE})"
        return NOCOPY_REMEDIATION_HINT

    def source_copies_into_storage(self, expr: 'TpyExpr | None',
                                   auto_moved: bool) -> bool:
        """Whether an owning slot (a field, a container element, an `Own`
        parameter) fed from `expr` holds a COPY of an object that outlives
        the store, where CPython would alias it: the source arrives borrowed
        (`arrives_borrowed`), is not an explicit `copy()` and is not an
        auto-moved last use (`auto_moved`, asked once by the caller since the
        question may retract a last-use mark). The one source rule every
        owning sink asks; the sink decides which slot types a copy is
        observable in."""
        return (expr is not None and self.arrives_borrowed(expr)
                and not self.is_copy_call(expr) and not auto_moved)

    def arrives_borrowed(self, expr: 'TpyExpr | None') -> bool:
        """The source names storage that outlives the expression, so an
        owning slot copies it: an lvalue (a ternary of lvalue arms included),
        a borrow-returning call (a tuple result carrying a borrowed element
        included), a walrus (whatever its value: it names the binding it
        just made, which outlives the store), or a MIXED-arm ternary whose
        name arm is a reference. The one question every `Own`
        slot and owned-storage member asks before its copy rule.

        A walrus renders `(d = &(c), *d)`, a read of the named object that
        never moves, so the bound name takes no last-use exemption. A
        reference-type ternary with an existing-object arm is an lvalue to
        the classifier, so `is_lvalue` answers it; the per-arm fallback below
        serves the ternaries it does not, and a protocol or record result is
        left to the lowering."""
        if expr is None:
            return False
        if (self.is_lvalue(expr) or self._tuple_call_carries_borrow(expr)
                or returns_borrow(self.ctx, expr)):
            return True
        inner = expr.expr if isinstance(expr, TpyCoerce) else expr
        if isinstance(inner, TpyNamedExpr):
            return True
        if not isinstance(inner, TpyIfExpr):
            return False
        result = unwrap_qualifiers(self.ctx.get_expr_type(inner))
        if isinstance(result, NominalType) and (result.is_protocol
                                                or result.is_user_record):
            return False
        return self.ternary_arm_copies(inner)

    def _ternary_member_copies(self, m: TpyExpr) -> bool:
        """A tuple literal member that is a ternary whose arms are names:
        each arm stores on its own, so a reference-typed NAME arm copies (the
        rule the field store has always applied per member). A scalar `Own`
        slot does not ask this -- a mixed-arm ternary there is left to the
        lowering, which refuses it."""
        inner = m.expr if isinstance(m, TpyCoerce) else m
        return isinstance(inner, TpyIfExpr) and self.ternary_arm_copies(inner)

    def ternary_arm_copies(self, expr: TpyIfExpr) -> bool:
        """Whether either arm of a ternary store source copies into storage.

        A name behind a ternary is not an lvalue of the whole expression
        unless both arms are, yet each arm stores on its own, so classify per
        arm. The arm render is a plain C++ `?:` operand and never a move (an
        owned local arm does not lower at all today), so a last-use mark on
        the name does not exempt it.
        """
        for arm in (expr.then_expr, expr.else_expr):
            if isinstance(arm, TpyIfExpr):
                if self.ternary_arm_copies(arm):
                    return True
                continue
            # A prvalue arm (constructor call, literal) materializes in place;
            # an arm the classifier calls an lvalue (an element, a field, a
            # borrow-returning call) names storage that outlives the store.
            if not isinstance(arm, TpyName):
                if not is_rvalue_source(self.ctx, arm):
                    at = self.ctx.get_expr_type(arm)
                    if (at is not None and not unwrap_qualifiers(
                            at).is_value_type()):
                        return True
                continue
            scope_type = (self.ctx.func.current_scope.lookup(arm.name)
                          if self.ctx.func.current_scope else None)
            if scope_type is None:
                continue
            if not unwrap_qualifiers(scope_type).is_value_type():
                return True
        return False

    def warn_storage_tuple_copy(self, elem: TpyExpr, tuple_type: TpyType,
                                dest: str, elem_path: str = "") -> bool:
        """Value-tuple-with-reference-member copy diagnostic for an
        owned-storage element. A literal and a whole-tuple lvalue need
        different gating (per-member-expr vs per-element-type), so dispatch on
        source shape. Returns whether anything fired.

        A borrow-carrying CALL result is an rvalue, so `is_lvalue` skips it --
        but its borrowed elements alias the caller exactly as an lvalue's do,
        and the sink copies them just the same, so it takes the per-element-type
        path too."""
        peeled = peel_value_wrappers(elem)
        if isinstance(peeled, TpyTupleLiteral):
            return self.check_tuple_literal_members(
                peeled, tuple_type, TupleSink.CONTAINER, dest, elem_path)
        copied = (self.auto_move_copied_elements(elem)
                  if self.is_lvalue(elem) else None)
        if copied is not None:
            # Only the elements the local holds by reference alias the
            # caller, so only they warn. The owned ones are copied at the
            # last use too, not moved:
            # BUGS.md#tuple-local-last-use-copies-owned-elements.
            if not copied:
                return False
            return self.warn_pointer_repr_tuple_copy(
                elem, tuple_type, dest, elem, elem_path, only=copied)
        if self.is_lvalue(elem) or self._tuple_call_carries_borrow(elem):
            return self.warn_pointer_repr_tuple_copy(
                elem, tuple_type, dest, elem, elem_path)
        return False

    def _derive_owning_storage(self, expr: TpyExpr) -> bool:
        """Whether a binding from `expr` makes the local OWN its tuple
        element storage: an owning-tuple call, a name already carrying the
        fact, or a ternary with an owning arm (the result aliases either,
        so it is hazardous if either is -- matching the fresh-fact merge).
        """
        inner = peel_value_wrappers(expr)
        if isinstance(inner, TpyIfExpr):
            return (self._derive_owning_storage(inner.then_expr)
                    or self._derive_owning_storage(inner.else_expr))
        if isinstance(inner, TpyName):
            return self.ctx.func.bp_is_owning_storage(inner.name)
        return self._is_owning_tuple_call(inner)

    def _derive_copy_into_own_hazards(self, init_expr: TpyExpr) -> list[int]:
        """Indices whose element the local holds by REFERENCE -- a plain
        borrow (param / attribute / non-last-use local) or an owned source
        not moved in -- so it copies into an Own[T] slot rather than moving.
        An explicit copy(), a fresh rvalue, a value type or an owned last-use
        (move) is NOT a hazard.

        Dispatches on provenance like `_derive_tuple_member_hazards`: a literal
        scans its elements; a bare-name alias inherits the source local's
        recorded hazards; a ternary UNIONs both arms (the result aliases
        either); a call returning a tuple that borrows marks each plain
        reference element of its declared return (a borrow of storage the
        caller still reaches -- an `Own` element is the callee's to hand over).
        """
        inner = init_expr.expr if isinstance(init_expr, TpyCoerce) else init_expr
        if isinstance(inner, TpyIfExpr):
            return sorted(
                set(self._derive_copy_into_own_hazards(inner.then_expr))
                | set(self._derive_copy_into_own_hazards(inner.else_expr)))
        if isinstance(inner, TpyName):
            return sorted(self.ctx.func.bp_copy_into_own_idxs(inner.name))
        rt = self._borrow_carrying_tuple_return(inner)
        if rt is not None:
            return [i for i, et in enumerate(rt.element_types)
                    if not isinstance(unwrap_readonly(et), OwnType)
                    and not unwrap_readonly(unwrap_ref_type(et)).is_value_type()]
        if not isinstance(inner, TpyTupleLiteral):
            return []
        return [i for i, elem in enumerate(inner.elements)
                if self.elem_is_plain_borrow(elem)
                or self._elem_copies_owned_into_own(elem)]

    def _elem_copies_owned_into_own(self, elem: TpyExpr) -> bool:
        """Whether `elem` is an owned source bound by reference that COPIES into
        an Own[T] slot: not copy(), an lvalue, not moved at last use, and OWNED
        (Own-typed, or an owned local of a reference type). A plain borrowed
        source is `elem_is_plain_borrow`'s case; both copy and warn."""
        if self.is_copy_call(elem) or not self.is_lvalue(elem):
            return False
        if self.is_owned_last_use_move(elem):
            return False
        raw = self.ctx.get_raw_expr_type(peel_value_wrappers(elem))
        if raw is None:
            return False
        unwrapped = unwrap_ref_type(raw)
        # An Own-typed source is owned-by-value (OwnType.is_value_type() is
        # True), so check it before the value-type gate below.
        if isinstance(unwrapped, OwnType):
            return True
        if unwrapped.is_value_type():
            return False
        return isinstance(elem, TpyName) and self._is_owned_var(elem.name)

    def is_owned_last_use_move(self, elem: TpyExpr) -> bool:
        """An owned local/param at its last use -- it MOVES into a tuple slot
        (the local then OWNS the moved element) rather than borrowing. Mirrors
        the scalar field/return auto-move; the local-context elem-capture and
        the owning-storage derivation share this condition."""
        return self.is_auto_move_use(elem)

    def elem_is_plain_borrow(self, elem: TpyExpr) -> bool:
        """Whether `elem` is a plain borrowed reference that would COPY (not
        move) into an Own[T] slot -- the per-element analog of the scalar
        `check_own_lvalue_into_own` borrowed-source copy, scoped to plain
        references (Own-typed and nocopy sources are handled elsewhere)."""
        if self.is_copy_call(elem):
            return False
        if not self.is_lvalue(elem):
            return False
        raw = self.ctx.get_raw_expr_type(peel_value_wrappers(elem))
        if raw is None:
            return False
        unwrapped = unwrap_ref_type(raw)
        if isinstance(unwrapped, OwnType) or unwrapped.is_value_type():
            return False
        # Owned local at its last use moves into the slot -- not a copy.
        if self.is_owned_last_use_move(elem):
            return False
        return True

    def _warn_name_element_copy(self, et: OwnType, i: int,
                                expr: TpyExpr) -> None:
        """Element `i` of a tuple NAME copies into its `Own` slot: the owning
        slot's warning (deferred to the instantiation for an open payload),
        or an error for a type with no copy to declare."""
        payload = et.wrapped
        if self._is_value_type_param(payload):
            return
        if self.ctx.is_type_non_copyable(payload):
            raise self.ctx.error(
                f"cannot copy non-copyable type '{payload}' into owned "
                f"storage (tuple element {i}){NOCOPY_REMEDIATION_HINT}", expr)
        where = f"owned storage (tuple element {i})"
        if not self.ctx.defer_own_copy_verdict(payload, payload, where, expr):
            self.ctx.warning(
                f"copies {payload} into {where}; use copy() to make this "
                f"explicit", expr)

    def record_name_copy(self, expr: TpyExpr | None,
                         copied: 'set[int] | None') -> None:
        """Declare the copy of a tuple NAME at an owning slot to the lowering,
        which builds only declared copies. `copied` holds the elements the
        warnings declared (None: every pointer-repr element). A binding that
        also holds an element by VALUE -- an owned element beside the
        borrowed ones -- is marked MIXED: copying the whole tuple would copy
        that element too, undeclared, so the lowering keeps rejecting it."""
        if not isinstance(expr, TpyName):
            return
        self.ctx.own_element_copies.add(expr)
        declared = self.ctx.func.current_scope.lookup(expr.name)
        dt = (unwrap_readonly(unwrap_ref_type(declared))
              if declared is not None else None)
        pure = isinstance(dt, TupleType)
        for i, et in enumerate(dt.element_types if pure else ()):
            e = unwrap_readonly(unwrap_ref_type(et))
            if isinstance(e, OwnType):
                pure = False
            elif isinstance(e, TupleType) and e.has_nested_pointer_repr_element():
                # A nested tuple is held by value, reference members and all.
                pure = False
            elif e.is_value_type():
                continue
            elif (not TupleType._element_is_pointer_repr(e)
                  if copied is None else i not in copied):
                pure = False
        if not pure:
            self.ctx.own_element_mixed.add(expr)

    def check_name_borrow_into_own(self, name: str, tuple_type: TupleType,
                                   expr: TpyExpr, *,
                                   return_slot: bool = False) -> None:
        """Warn when a tuple LOCAL referenced by `name` feeds a borrowed
        element into an `Own[T]` slot of the contextual `tuple_type` -- the
        implicit copy the scalar `Own[T]` and the literal-tuple forms declare
        the same way. The literal forms go through
        `check_tuple_literal_members`; this covers the deferred NAME path via
        the construction-time hazard fact (BindingProvenance)."""
        copied: set[int] = set()
        for i, et in enumerate(tuple_type.element_types):
            if not isinstance(et, OwnType):
                continue
            # A borrowed source, or an owned one bound by reference (not
            # moved), copies into the Own slot -- the deferred-name analog of
            # the scalar T->Own[T] copy warning. A moved owned source is
            # storage form and carries no such hazard.
            if i in self.ctx.func.bp_copy_into_own_idxs(name):
                self._warn_name_element_copy(et, i, expr)
                copied.add(i)
        # A tuple PARAM or LOOP VARIABLE carries no construction-time facts:
        # its declared elements say what arrives borrowed. (At an argument
        # the whole-tuple coercion warning already declares its copy; a
        # return has none.)
        func = self.ctx.func
        if return_slot and (
                (name in func.current_param_names
                 and name not in func.current_rebound_params)
                or (name in func.loop_vars
                    and name not in func.current_reassigned_vars)):
            declared = self.ctx.func.current_scope.lookup(name)
            dt = (unwrap_readonly(unwrap_ref_type(declared))
                  if declared is not None else None)
            if (isinstance(dt, TupleType)
                    and len(dt.element_types) == len(tuple_type.element_types)):
                for i, (st, et) in enumerate(zip(dt.element_types,
                                                 tuple_type.element_types)):
                    src = unwrap_readonly(st)
                    if (isinstance(et, OwnType) and not isinstance(src, OwnType)
                            and not src.is_value_type()):
                        self._warn_name_element_copy(et, i, expr)
                        copied.add(i)
        if copied:
            self.record_name_copy(expr, copied)
        # A movable owned-tuple source at its last use MOVES into the owned
        # sink (forward as arg, or return), so it is consumed -- mark it for
        # the unconsumed-param warning. Borrowed/copied sources took the
        # raise/warn paths above (is_auto_move_use is False for them), so they
        # are correctly left unconsumed.
        if isinstance(expr, TpyName) and self.is_auto_move_use(expr):
            self.check_own_consumption(expr)

    def _derive_tuple_member_hazards(
            self, tt: TupleType, expr: TpyExpr) -> int | None:
        """Fresh-dangle borrow-member hazard index for an init expr bound to a
        tuple local of type `tt`, or None.

        Dispatches on provenance:
        - tuple literal: scan its elements (`_tuple_literal_fresh_borrow_elem`).
        - bare name: inherit the source local's already-recorded fact, re-checked
          against `tt` so an `Own[T]` boundary element (the escape) is dropped.
        - ternary: UNION the two arms -- the result aliases either, so it is
          hazardous if either is (conservative over-rejection, matching the
          flow_facts branch merge).
        Other init shapes (calls, etc.) carry no fact: a call can only return a
        durable borrow (its own fresh members are rejected at its return), and
        durable members alias safely in pointer borrow form.
        """
        inner = expr.expr if isinstance(expr, TpyCoerce) else expr
        if isinstance(inner, TpyIfExpr):
            then_f = self._derive_tuple_member_hazards(tt, inner.then_expr)
            else_f = self._derive_tuple_member_hazards(tt, inner.else_expr)
            return then_f if then_f is not None else else_f
        if isinstance(inner, TpyName):
            fresh = self.ctx.func.bp_owns_fresh_idx(inner.name)
            if fresh is not None and not self._tuple_elem_still_borrow(tt, fresh):
                fresh = None
            return fresh
        return self._tuple_literal_fresh_borrow_elem(tt, inner)

    def _check_tuple_member_local(self, tuple_type: TupleType,
                                  expr: TpyExpr, verb: str) -> None:
        """Reject a bare-name yield/return of a tuple local bound from a literal
        with a non-value member (the non-literal remainder of
        `_check_tuple_elem_dangle`).

        The literal check only sees a `TpyTupleLiteral` at the boundary; here the
        source is a `TpyName` whose binding was flagged at assignment. Loop /
        param-derived tuple locals are never flagged, so borrow composition
        (`for pair in src: yield pair`) is unaffected. The two facts are checked
        fresh-first: if a still-applicable fresh (dangle) hazard exists it is
        reported, otherwise the durable (silent-copy) one is -- so a local that
        trips both surfaces the dangle. The two facts may flag different element
        indices; the re-check below resolves each against the boundary type.
        """
        inner = peel_value_wrappers(expr)
        # A ternary returns whichever arm is taken -- check both.
        if isinstance(inner, TpyIfExpr):
            self._check_tuple_member_local(tuple_type, inner.then_expr, verb)
            self._check_tuple_member_local(tuple_type, inner.else_expr, verb)
            return
        if not isinstance(inner, TpyName):
            return
        fresh = self.ctx.func.bp_owns_fresh_idx(inner.name)
        if fresh is not None and self._tuple_elem_still_borrow(tuple_type, fresh):
            et = tuple_type.element_types[fresh]
            raise self.ctx.error(
                f"Cannot {verb} tuple local '{inner.name}': element {fresh} "
                f"('{et}') owns a freshly constructed value that is {verb}ed "
                f"by reference and would dangle. Use Own[{et}] for this tuple "
                f"element to {verb} by value.",
                inner
            )
        # The durable (non-dangling) reference-member case needs no check: a
        # tuple local is pointer borrow form (std::tuple<..., T*>), so
        # yielding/returning it ALIASES the durable member exactly as CPython
        # shares it. Only the fresh-dangle case above is rejected -- a locally
        # constructed member dangles even as a pointer.

    def _check_tuple_storage_return_root(self, tuple_type: TupleType,
                                         expr: TpyExpr) -> None:
        """Reject a borrow-form tuple RETURN read from non-durable storage.

        Returning a storage-form source (field / subscript) lifts element
        ADDRESSES into that storage (codegen's tuple_to_pointer), so the
        storage root must outlive the call: non-Own params, non-consuming
        self, and globals qualify; locals, temporaries, Own params, and
        consuming-method self die at exit. Yields are exempt -- generator
        storage is frame-rooted (alive while the consumer iterates) and the
        consumer-side ephemeral-escape checks cover retention.
        """
        if not tuple_type.has_pointer_repr_element():
            return
        inner = peel_value_wrappers(expr)
        # A ternary returns whichever arm is taken -- check both.
        if isinstance(inner, TpyIfExpr):
            self._check_tuple_storage_return_root(tuple_type, inner.then_expr)
            self._check_tuple_storage_return_root(tuple_type, inner.else_expr)
            return
        dangles = False
        if isinstance(inner, (TpyFieldAccess, TpySubscript)):
            dangles = self.is_dangling_return(inner)
        elif isinstance(inner, (TpyCall, TpyMethodCall)):
            # A relayed call: the callee's return_borrows_from names which
            # receiver/args the returned pointers root in -- dangling iff
            # that source is. An owning-rvalue return (Own[tuple] or
            # per-element Own) is a dying temporary: lifting it dangles.
            dangles = (self.is_dangling_return(inner)
                       or self._is_owning_tuple_call(inner))
        elif isinstance(inner, TpyName):
            # A local that aliases STORAGE (t = items[0] / t = h.pair / loop
            # var over a container): climb the borrow chain / loop provenance
            # to the storage root. Only chains crossing an ELEMENT/FIELD/PTR/
            # ITER borrow point INTO storage that must outlive the call;
            # ALIAS-only chains and literal-bound locals merely copy the
            # tuple's pointer slots, whose safety the owns-fresh facts cover
            # -- UNLESS the chain bottoms out in a local that OWNS its
            # element storage (bound from an owning-tuple call), where the
            # lift would point into the dying owner.
            bt = self.ctx.func.borrow_tracker
            # Owning takes precedence: a local bound from an owning-tuple call
            # on ANY reaching path (UNION-merged) points into a function-local
            # slot that dies at return -- unsafe even if another path aliases
            # param storage (a branch-mixed local also carries that path's
            # FIELD borrow, which would otherwise mask the owning path here).
            if (self.ctx.func.bp_is_owning_storage(inner.name)
                    or self.ctx.func.bp_is_owning_storage(
                        bt.effective_storage(inner.name))):
                dangles = True
            elif self._borrow_chain_enters_storage(bt, inner.name):
                # EVERY root must be caller-owned: a re-seated name aliases a
                # function-local on one of its paths and the return would point
                # into it, so one unsafe root dangles the tuple.
                srcs = bt.storage_roots_or_self(inner.name)
                dangles = not all(
                    self._name_is_param_or_global(_storage_root(src))
                    for src in srcs)
            else:
                its = self.ctx.func.loop_var_iterable.get(inner.name)
                if its:
                    # Any source outside param/global storage dies at return.
                    dangles = any(not self._name_is_param_or_global(
                        _storage_root(it)) for it in its)
        if dangles:
            bad = next(et for et in tuple_type.element_types
                       if tuple_type._element_is_pointer_repr(et))
            raise self.ctx.error(
                f"Cannot return this tuple: its non-value elements are "
                f"returned by reference into storage owned by the function, "
                f"which dies when it returns. Return a tuple rooted in "
                f"parameter data, or use Own[{unwrap_readonly(bad)}] for "
                f"such elements to return by value.",
                inner
            )

    @staticmethod
    def _borrow_chain_enters_storage(bt: BorrowTracker, name: str) -> bool:
        """Whether `name`'s borrow chain crosses a storage-entering borrow
        (ELEMENT/FIELD/PTR/ITER) -- i.e. holds addresses INTO a container or
        object rather than a value-copy of another local. ALIAS links are
        followed transitively; chains are acyclic so the walk terminates.
        """
        seen: set[str] = set()
        cur = name
        while cur not in seen:
            seen.add(cur)
            kind = bt.borrow_kind_of(cur)
            if kind is None:
                return False
            if kind is not BorrowKind.ALIAS:
                return True
            nxt = bt.borrow_source(cur)
            if nxt is None:
                return False
            cur = nxt
        return False

    def _nested_tuple_borrow_member(self, tuple_type: TupleType,
                                    _nested: bool = False) -> TpyType | None:
        """Return a borrow-form member type nested inside an inner tuple, or None.

        Codegen's tuple borrow/storage conversion is flat: a non-value member
        carried by an *inner* tuple slot (`std::tuple<..., std::tuple<..., T*>>`)
        is miscompiled. A top-level borrow member (`_nested` False) is fine and
        excluded; `Own` / value / wrapper / type-param members are value-stored
        and excluded at any depth.
        """
        for et in tuple_type.element_types:
            bare = unwrap_readonly(et)
            if isinstance(bare, TupleType):
                deeper = self._nested_tuple_borrow_member(bare, _nested=True)
                if deeper is not None:
                    return deeper
            elif (_nested and not et.is_value_type()
                    and not isinstance(et, (OwnType, TypeParamRef))
                    and not et.needs_wrapper()):
                return et
        return None
