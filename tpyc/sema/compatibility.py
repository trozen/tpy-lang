"""
TurboPython Type Compatibility

Type compatibility checking, coercions, and lvalue analysis.
"""

from __future__ import annotations
import math
import re
from dataclasses import replace as dc_replace
from enum import Enum, auto
from typing import (TYPE_CHECKING, Callable, Iterable, Iterator, NamedTuple,
                    Optional, Sequence, TypeGuard)

from ..type_def_registry import has_view_param_form
from ..typesys import pointer_repr_optional, readonly_access, resolve_int_literals, BIGINT
from ..typesys import (
    is_value_type_bound,
    same_class, Representation, return_representation,
    make_readonly,
    ELEM,
    TpyType, IntLiteralType, FloatLiteralType, ListRepeatType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingBytesType, PendingViewType, UnknownElementType,
    view_family_for_type,
    LiteralType, LiteralValue, LiteralTag, FLOAT, make_list, make_dict, make_set,
    OwnType, ReadonlyType, VoidType, PtrType, is_readonly_ptr, TupleType,
    NominalType, AliasRef, RecursiveAliasInstanceType, TypeParamRef, TypeParamKind, NoneType, AnyType, OptionalType, UnionType,
    is_protocol_type, is_dyn_protocol, unwrap_own, strip_own_type_args, unwrap_readonly, unwrap_optional_own, owned_payload, drop_own_marker,
    contains_type_param,
    is_any_str_type, get_covariant_params, PendingGenericInstanceType,
    CallableType, is_fn_type, RefType, unwrap_ref_type,
    is_callable_type, is_integer_type, is_any_float_type, is_readonly_span,
    unify_literal_types,
    is_polymorphic_class_type, is_exception_type, SendType, SyncType, unwrap_send_sync, FrameType,
    unwrap_qualifiers,
    ConcreteCoroType,
    GenExprType, PendingNumType,
    global_binds_by_reference, own_tuple_target,
    param_has_mutable_borrow_surface)
from .. import qnames
from ..identity_map import IdentityMap
from ..value_category import (
    async_result_aliases,
    CONTAINER_LITERAL_NODES, getitem_call_of, is_iterator_protocol,
    is_rvalue_source,
    peel_coerce, peel_value_wrappers, const_place,
    accessor_returns_ref, returns_borrow)
from . import own_copy
from .alias_rebind import (ElemPath, bare_tuple, declared_call_paths,
                           is_reference_leaf,
                           leaf_paths, owned_slot_paths,
                           peel_slot, pointer_tuple_names, reference_paths,
                           top_level, tuple_borrowed_paths, tuple_elem_owned,
                           tuple_source_elem_owned)
from .binding_ownership import (CompMove, ReadKind, bound_name,
                                owned_head_targets,
                                read_ownership, record_body_ownership,
                                resolve_read, site_ownership)
from .source_records import (Leaf as TupleLeaf, LeafPermission,
                             SinkRead, SourceRecord, read_record, verdict,
                             publish_source_record)
from .type_join import python_type_name, user_type_name
from .frame_traits import frame_traits_of_function, frame_type_of_function
from .send_chain import why_not_send, why_not_sync, why_not_frame, render_chain
from .move_chain import why_not_movable, render_move_chain
from ..parse import (
    const_tuple_index,
    ResultForm,
    TpyExpr, TpyName, TpyFieldAccess, TpySubscript, TpyArrayLiteral,
    TpyListRepeat, TpyCall, TpyCallLike, TpyMethodCall, TpyUnaryOp,
    TpyBinOp, TpyCoerce, TpyNoneLiteral, TpyIntLiteral,
    TpyFunction, TpyIfExpr, TpyTupleLiteral, TpyLambda, TpyNamedExpr,
    TpyAwait, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral, TpyBytesLiteral,
    lambda_of, comp_free_reads, comp_sinks, TpyComprehensionGenerator,
    TpyStmt, TpyVarDecl, TpyForEach, SourceLocation, TpySlice
)
from .literal_utils import literal_value_from_expr
from ..parse.nodes import program_names, walk_body_stmts, walk_expr_tree
from .dyn_erasure import dyn_erasure_coercion
from ..coercions import resolve_coercion, borrow_only_veto, Coercion, CoercionContext, DEREF_COERCION, UPCAST_TO_PTR, UPCAST_TO_CONST_PTR, SPAN_METHOD_TO_SPAN_ARG, SPAN_METHOD_TO_SPAN, INTO_ANY, FROM_ANY, literal_range_error, coercion_refuses_value, int_payload
from ..modules import get_span_return_type
from .call_lends import (
    EveryTempLend, Lent, MayLend, ReturnLend,
    borrowing_ctor_arg, call_result_is_fresh, escaping_borrow_tuple,
    every_lent_operand,
)
from .context import (
    CompTarget, ConsumingLoop, FinallyCapture, ProbeSlot,
    OwnIterMove, OwningRead, PendingCopy, SinkCopy,
    UnneededCopy, UnpackClaim, OwnSlot, ParkedFunctionState, ParkedRebind,
    BorrowKind,
    BorrowTracker, SitedKey, holds_generator_object, paths_overlap,
)
from ..storage_trace import StepKind
from .storage_walk import (Leaf, LeafKind, MarkRequest, StorageKey, Use,
                           leaves, mark_roots, slot_lent_roots,
                           storage_roots, ternary_operands)
from .numeric_lattice import (fixed_int_range_contains, numeric_info,
                             smallest_common_int)
from ..diagnostics import (
    Diagnostic, DiagnosticLevel, SemanticError, NOCOPY_REMEDIATION_HINT, NOCOPY_NO_AUTO_MOVE_HINT,
    insert_in_source_order,
    CONSUMING_FIELD_MOVE_NOTE, CONSUMING_FIELD_COPY_CLAUSE,
    ITERABLE_LVALUE_COPY_HINT)
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_varargs, is_span_iter, is_list,
    is_str_view_type, is_bytes_view_type, is_borrowing_view_type, int_traits_of,
    is_big_int_type, is_fixed_int_type, is_str_category, is_bytes_category,
    protocol_info_of, iter_yields_owned_elements, is_iterator_adapter,
    is_dict_view,
)
from .overloads import type_matches_numeric
from .int_narrowing import narrowing_proven_safe, site_value_range
from .pending_num import (ContainerCells, at_path, container_parts,
                          is_numeric_slot, is_pending_num, literal_entries,
                          map_leaves,
                          pending_leaves, pending_list_of, root_of, strip_int,
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


def literal_conversion_source(actual: TpyType, expected: TpyType) -> TpyType:
    """The type `actual` converts from into `expected`: an int `Literal[...]`
    value renders as its storage type, so at an integer slot it converts as
    that type does (`int32` -> `int` is `::tpy::BigInt(k)`)."""
    if (isinstance(actual, LiteralType) and actual.is_int_base()
            and is_integer_type(expected)):
        return actual.base_type
    return actual


def _view_return_family(return_type: TpyType) -> tuple[str, str] | None:
    """(display name, owning alternatives) for a borrowing-view return type."""
    for pred, display, owned in _VIEW_RETURN_FAMILIES:
        if pred(return_type):
            return display, owned
    return None


_TUPLE_ELEMENT_SUFFIX = re.compile(r" \(tuple element ([0-9.]+)\)$")


def _element_at(t: TupleType, path: ElemPath, *,
                keep_markers: bool = False) -> TpyType:
    """The element type at `path` (`reference_paths`) of tuple `t`, its
    `Own` marker dropped, and its `Send` / `Sync` markers too unless
    `keep_markers`."""
    for i in path[:-1]:
        t = unwrap_own(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            t.element_types[i]))))
    leaf = t.element_types[path[-1]]
    return unwrap_own(leaf if keep_markers else unwrap_send_sync(leaf))


def _element_payload(t: TpyType) -> TpyType:
    """A tuple element's type with its `Own` marker dropped, as the copy
    warnings name it (`Send[Own[T]]` names `Send[T]`)."""
    if isinstance(t, OwnType):
        return t.wrapped
    if unwrap_send_sync(t) is not t:
        return dc_replace(t, wrapped=_element_payload(t.wrapped))
    return t


def _tuple_element_context(context: str, i: int) -> str:
    """`context` narrowed to element `i`; a nested element extends the
    enclosing element's path (`1.0`), as the copy warnings spell it."""
    m = _TUPLE_ELEMENT_SUFFIX.search(context)
    if m is None:
        return f"{context} (tuple element {i})"
    return f"{context[:m.start()]} (tuple element {m.group(1)}.{i})"


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


def result_owns_storage(slot: TpyType) -> bool:
    """Whether a result slot of type `slot` holds its value in storage of
    its own: an `Own[...]`, or a by-value result -- a plain reference-type
    result is a borrow, a view's storage is someone else's. The record's
    per-leaf `_slot_owned_paths` asks it of a scalar's one leaf."""
    bare = unwrap_readonly(unwrap_ref_type(slot))
    return (isinstance(bare, OwnType)
            or return_representation(bare) is Representation.STORAGE)


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
    # A store into `Any`, which owns the payload it erases. No literal lands
    # here member by member: it names a registration's sink kind only.
    ANY = auto()
    # An unpack of a tuple NAME: its targets take the elements it hands
    # over and refer to the others. A registration's sink kind only.
    UNPACK = auto()


_BORROW_FORM_SINKS = frozenset({TupleSink.RETURN, TupleSink.ARG,
                                TupleSink.YIELD, TupleSink.LOCAL})
# The sinks whose codegen MOVES a last-use owned member into the slot; the
# others build the literal into storage element by element, which copies
# even then.
# CONTAINER stays out although the subscript STORE builds its literal by
# value now: the sink also names a container LITERAL's element, which still
# lifts, and the rule must not exempt a `@nocopy` member there.
_LAST_USE_MOVES_SINKS = frozenset({TupleSink.FIELD, TupleSink.YIELD})
# The sinks that hold a nested tuple member as owned storage, whatever the
# literal's declared element type marks (`nested_member_sink`); a return, a
# yield and an argument own what their declared slot's `Own` markers say.
_STORAGE_SINKS = frozenset({TupleSink.LOCAL, TupleSink.FIELD,
                            TupleSink.CONTAINER})
_NEW_LITERALS = CONTAINER_LITERAL_NODES + (
    TpyNoneLiteral, TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
    TpyStrLiteral, TpyBytesLiteral)


def _produces_result(expr: TpyExpr) -> bool:
    """A call, an `await`, an operator (a builtin one builds its value; a
    dunder is a call) or a subscript dispatched to `__getitem__`: a
    result `returns_borrow` classifies."""
    if isinstance(expr, (TpyCallLike, TpyAwait, TpyBinOp, TpyUnaryOp)):
        return True
    return (isinstance(expr, TpySubscript)
            and getitem_call_of(expr) is not None)


# A closure's return of a name it captured: the closure reads the capture in
# place, so neither auto-move nor a copy() of a non-copyable value applies.
NOCOPY_CAPTURE_HINT = (
    " (a closure reads its captured value in place and cannot move it out; "
    "pass the value in as a parameter to transfer ownership)"
)


def _diag_line(d: Diagnostic) -> float:
    # A diagnostic with no line goes last (`insert_in_source_order`).
    return d.loc.line if d.loc is not None else math.inf


def open_tuple_leaf(t: TpyType) -> bool:
    """Whether a tuple leaf of type `t` is an open type parameter, whose
    copy only an instantiation decides: its copy is HEDGED, not WARNED."""
    return isinstance(unwrap_readonly(unwrap_ref_type(unwrap_own(t))),
                      TypeParamRef)


def own_slot_member(sink: 'TupleSink | None', owned: bool) -> bool:
    """Whether a tuple literal's member at `sink` (`owned`: its element
    type is `Own`-marked) keeps its slot's `Own` rule -- an `Own`-marked
    member at a RETURN or an ARG -- rather than the rule the sink gives an
    unmarked member."""
    return owned and sink in (TupleSink.RETURN, TupleSink.ARG)


def literal_member_storage_copy(sink: 'TupleSink | None', owned: bool,
                                member_t: TpyType, m: TpyExpr) -> bool:
    """Whether a scalar member `m` of a tuple literal at `sink` takes the
    owned-storage member rule, under which a borrowed member is copied and
    warned (`check_member_read`). `member_t` is the member's
    element type and `owned` says it is `Own`-marked. Not a member of an
    `Own` slot (sink None) nor one that keeps its slot's `Own` rule
    (`own_slot_member`): that rule declares its copy. Not a value type,
    nor an open `T` (its copy is the instantiation's), nor an unmarked
    member at a sink that holds it in borrow form -- but a call's at a
    LOCAL."""
    if sink is None or own_slot_member(sink, owned):
        return False
    payload = owned_payload(member_t)
    if payload is None:
        payload = member_t
    if payload.is_value_type() or open_tuple_leaf(payload):
        return False
    return (owned or sink not in _BORROW_FORM_SINKS
            or (sink is TupleSink.LOCAL and _call_member(m)))


def member_owns_every_leaf(sink: 'TupleSink | None', owned: bool,
                           whole: bool) -> bool:
    """Whether the slot of a tuple literal's nested value-tuple member owns
    every leaf of it: an `Own`-marked element (`owned`), a member of an
    `Own[tuple[...]]` slot (`whole`), or one of owned storage
    (`_STORAGE_SINKS`). Elsewhere the declared slot's `Own` markers say
    which leaves it owns."""
    return owned or whole or sink in _STORAGE_SINKS


def nested_member_sink(sink: 'TupleSink | None', owned: bool,
                       whole: bool) -> 'TupleSink | None':
    """The sink the members of a tuple literal's nested value-tuple member
    (its element `owned` when `Own`-marked, `whole` under an
    `Own[tuple[...]]` slot) are checked at: owned storage, a container
    element's, where the member's slot owns every leaf
    (`member_owns_every_leaf`) -- but a return and a yield keep their own,
    whose `Own` rule words the copy, as does an `Own`-marked member of a
    return or an argument (`own_slot_member`). None (an `Own` slot's
    literal, its members the caller's) stays None."""
    if (sink is None or sink in (TupleSink.RETURN, TupleSink.YIELD)
            or own_slot_member(sink, owned)
            or not member_owns_every_leaf(sink, owned, whole)):
        return sink
    return TupleSink.CONTAINER


def _call_member(m: TpyExpr) -> bool:
    """A tuple-literal member that is a call (`(b.get(), 1)`,
    `(d.get(k, fb), 1)`): a LOCAL holds its result by value, not by
    pointer -- a lent result's element has no pointer render yet (TODO.md
    "Sink capture keys on the shared lending verdict") -- so it is owned
    storage there and takes that slot's copy rule, as the binding's record
    does."""
    return isinstance(peel_value_wrappers(m), TpyCallLike)


class CompatError:
    """Type compatibility check failure (returned by _check_compat, not raised)."""
    __slots__ = ('message', 'loc', 'copy_admits', 'tuple_copy_remedy')

    def __init__(self, message: str, loc: SourceLocation | None = None, *,
                 copy_admits: bool = False):
        self.message = message
        self.loc = loc
        # A readonly reference met a mutable reference slot, which a `copy()`
        # of the source would fill.
        self.copy_admits = copy_admits
        # ...as an element of a readonly tuple: an owning slot names
        # `copy()` of the whole source as the remedy.
        self.tuple_copy_remedy = False


# Result type for _check_compat: Coercion | None (compatible) or CompatError (incompatible)
CompatResult = Coercion | CompatError | None


class UnionLanding:
    """The union member a committing check landed its value in, written by
    the check's union arm so the fact a caller stamps is the check's own
    decision."""
    __slots__ = ('member',)

    def __init__(self) -> None:
        self.member: 'TpyType | None' = None


if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .methods import MethodAnalyzer
    from .local_deduction import LocalTypeDeduction
    from .pending_num import PendingNums
    from .init_tracker import InitTracker
    from ..typesys import FunctionInfo
    from .value_range import ValueRange


# `site_coercion`'s default: the value range is read where it is asked.
_READ_NOW = object()


class InferredSlot(NamedTuple):
    """A slot typed by a type argument a call inferred for local `local`
    (bound at `line`), not one the user wrote: `what` names the slot."""
    local: str
    line: int
    what: str


def inferred_receiver(ctx: 'SemanticContext', obj: TpyExpr
                      ) -> 'tuple[str, int, NominalType] | None':
    """`obj` as a local whose type arguments an earlier call inferred
    (`c = Box(); c.set(int32(1))`): its name, the line of its binding and
    its resolved type; None for anything else."""
    if not isinstance(obj, TpyName):
        return None
    known = ctx.func.inferred_generic_locals.get(obj.name)
    if known is None:
        return None
    resolved, line = known
    if unwrap_readonly(unwrap_ref_type(ctx.get_expr_type(obj))) != resolved:
        return None
    return obj.name, line, resolved


def inferred_field_slot(ctx: 'SemanticContext', target: TpyExpr
                        ) -> 'InferredSlot | None':
    """A store target that is a field its record types by a type parameter,
    of a local whose type arguments a call inferred."""
    if not isinstance(target, TpyFieldAccess):
        return None
    recv = inferred_receiver(ctx, target.obj)
    if recv is None:
        return None
    name, line, resolved = recv
    record = ctx.registry.get_record_for_type(resolved)
    fld = (next((f for f in ctx.registry.get_all_fields(record)
                 if f.name == target.field), None)
           if record is not None else None)
    if fld is None or not contains_type_param(fld.type):
        return None
    return InferredSlot(name, line, f"field '{target.field}'")


class PendingIterCopyCheck(NamedTuple):
    """An element-copy check on an iterator source whose lending callee's
    borrow facts were still pending when the copy was analyzed. `copied`
    is the source the owning slot takes, whose copy the warning declares
    (`source_expr` is where its elements are lent from)."""
    source_expr: TpyExpr
    elem_type: TpyType
    display_type: TpyType
    hint: str
    loc: SourceLocation | None
    copied: TpyExpr | None


class TempRoot(NamedTuple):
    """A temporary a borrow points into (`borrow_temp_roots`): `expr`, or
    with `in_tuple` the owned element `expr` of a temporary tuple the
    borrow points into."""
    expr: TpyExpr
    in_tuple: bool = False


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


def owned_tuple_slots(tt: TupleType,
                      owned: 'frozenset[ElemPath] | None' = None) -> TupleType:
    """`tt` with each top-level reference element its slot owns marked
    `Own` -- every one at a slot owning the whole tuple, else those whose
    path is in `owned`: the elements a tuple read there moves out."""
    return TupleType(tuple(
        et if (et.is_value_type() or isinstance(et, OwnType)
               or (owned is not None and (i,) not in owned)) else OwnType(et)
        for i, et in enumerate(tt.element_types)))


def _declared(reg: SinkRead, leaves: dict[ElemPath, TupleLeaf]
              ) -> dict[ElemPath, TupleLeaf]:
    """`leaves` with the implicit copies of a sink no check declares
    (`SinkRead.declares_copies`) recorded UNDECLARED."""
    if reg.declares_copies:
        return leaves
    return {p: dc_replace(leaf, permission=LeafPermission.UNDECLARED)
            if leaf.permission.copies else leaf
            for p, leaf in leaves.items()}


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
        # Set after construction, from the statement analyzer.
        self.init: 'InitTracker'

    def resolve_pending_iter_copy_checks(self) -> None:
        """Decide the element-copy checks deferred on a pending callee, now
        that every body's borrow facts are final -- so the verdict does not
        depend on whether the generator is defined above or below its use."""
        for check in self.pending_iter_copy_checks:
            if iteration_copies_lent_reference(
                    self.ctx, check.source_expr, check.elem_type):
                self._element_copy(check.elem_type, check.display_type,
                                   check.hint, check.loc, check.copied)
        self.pending_iter_copy_checks.clear()

    def _element_copy(self, elem_type: TpyType, display_type: TpyType,
                      hint: str, loc: SourceLocation | None,
                      source: TpyExpr | None) -> None:
        """An owning element slot copies each element of `source`, which
        lends them: warned, or refused for a non-copyable element."""
        if self.ctx.is_type_non_copyable(elem_type):
            _, msg = own_copy.slot_message(
                display_type, display_type, "owned storage",
                own_copy.KIND_ELEMENTS, hint, non_copyable=True)
            self.ctx.copy_error(msg, loc, source)
            return
        self.ctx.copy_warning(
            f"copies {self.diag_type(display_type)} elements; {hint}", loc,
            source)

    def _mark_addr_taken(self, expr: TpyExpr) -> None:
        """Mark all param roots of expr as mutated because their address is taken.

        Resolves alias / element / field / ptr borrow chains via
        `effective_storage` so element-borrowing aliases (`v = items[i]`)
        trace back to their underlying param. Loop variables also propagate
        to their source iterables -- both are storage that the address-take
        could mutate via the resulting pointer.
        """
        self.ctx.assert_slot_effect("an address-taken mark")
        mark_roots(self.ctx, storage_roots(expr, Use.PLACE,
                                           self.ctx.get_expr_type,
                                           MayLend(self.ctx)),
                   MarkRequest(loop_vars=True, effective_storage=True))

    def is_type_compatible(self, actual: TpyType, expected: TpyType,
                           coercion_ctx: CoercionContext | None = None) -> bool:
        """Non-raising check: is actual assignable to expected (in
        `coercion_ctx`: an argument converts as one does)? A query: it
        writes nothing -- no container literal's element or storage, no
        pending generic instance -- so an overload candidate can ask it and
        lose without leaving a trace (`ProbeSlot.QUERY`)."""
        with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
            return not isinstance(
                self._check_compat(actual, expected, "",
                                   coercion_ctx=coercion_ctx),
                CompatError)

    def _list_at_container(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None, source_expr: TpyExpr | None,
        coercion_ctx: CoercionContext | None,
    ) -> 'TpyType | CompatError | None':
        """A list literal checked against a slot that holds a typed
        container of numbers (`PendingNums.slot_containers`). One whose
        element a cell decides may be confirmed by the container or widened
        within its family: returns the list as the container sees it, the
        refusal, or None when `expected` holds no such container. The
        element is DECIDED here only where the slot step commits
        (`SemanticContext.slot_step_commits`), outside an overload trial;
        any other gets the verdict alone. A literal with no cell adapts to the container,
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
        if self.ctx.slot_step_commits() and not self.ctx.trial_depth:
            self.ctx.assert_slot_effect("a list literal's decision")
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
        receives, so it goes through the coercion check to that slot,
        which decides the list where the select's own coercion -- of the
        joined type -- no longer can (a slot step that does not commit asks
        for the verdict alone: `slot_step_commits`). Returns the operand's
        type as the slot sees it."""
        result = self._check_compat(
            actual, expected, "select operand",
            getattr(source_expr, "loc", None), source_expr,
            coercion_ctx=coercion_ctx)
        if isinstance(result, CompatError):
            raise SemanticError(result.message, result.loc)
        return self.pend.lists_as_known(actual)

    def _written_arg_entries(self, actual: TpyType, expected: TpyType,
                             ) -> 'list[tuple[object, ...]] | None':
        """The entries of a dict or set literal written as a call argument
        and scored open against several candidates
        (`ExpressionAnalyzer._probe_keeps_open`: no local, no cells, its
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
        # not a rebind, so the prescan leaves the name eligible.) The fact is
        # the LOCAL's: a binding site reusing the name (an `except` target)
        # is another binding.
        if (isinstance(source_expr, TpyName)
                and self.ctx.func is not None
                and source_expr.name in self.ctx.func.current_fresh_ctor_locals
                and resolve_read(self.ctx, source_expr.name).kind
                is ReadKind.LOCAL):
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
        target = unwrap_ref_type(unwrap_own(unwrap_readonly(expected)))
        if is_readonly_ptr(actual) and actual.inner_pointee == target:
            # The deref would hand out the readonly pointee at a slot that
            # borrows it (`check_type_compatible`'s copying-slot rule).
            if coercion_ctx in (CoercionContext.ARG, CoercionContext.RETURN):
                return f" -- declare {coercion_ctx.slot} 'readonly[{target}]'"
            return (f" -- keep the pointer: bind it to a name without an "
                    f"annotation or to a '{actual}' slot")
        if borrow_only_veto(actual, expected, coercion_ctx, sink_owns) is None:
            return ""
        return f" -- write '{target}(...)' around it"

    def _narrowed_member_access(self, actual: TpyType,
                                source_expr: 'TpyExpr | None') -> TpyType:
        """A name narrowed to one member of a readonly union reaches that
        member read-only, as a narrowed readonly Optional reaches its inner."""
        inner = source_expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        scope = self.ctx.func.current_scope
        if (not isinstance(inner, TpyName) or scope is None
                or isinstance(unwrap_ref_type(actual), ReadonlyType)):
            return actual
        declared = scope.lookup(inner.name)
        if (declared is not None and readonly_access(declared)
                and isinstance(unwrap_readonly(unwrap_ref_type(declared)),
                               UnionType)):
            return make_readonly(actual)
        return actual

    def _check_readonly_return(self, actual: TpyType, expected: TpyType,
                               loc: SourceLocation | None) -> None:
        """The declared return type is the contract of the slot: a readonly
        borrow does not fit a mutable reference return. A slot that copies
        (a value type, `Own[...]`) or a readonly-only protocol takes it."""
        a = unwrap_ref_type(unwrap_send_sync(actual))
        e = unwrap_ref_type(unwrap_send_sync(expected))
        if (not isinstance(a, ReadonlyType) or a.wrapped.is_value_type()
                or isinstance(e, (ReadonlyType, OwnType)) or e.is_value_type()):
            return
        # A union return with a readonly member takes the readonly borrow there.
        if isinstance(e, UnionType) and any(
                isinstance(unwrap_ref_type(m), ReadonlyType) for m in e.members):
            return
        if is_protocol_type(e) and self.protocols:
            proto_info = protocol_info_of(e)
            if proto_info and self.protocols.is_all_readonly(proto_info):
                return
        raise SemanticError(
            f"Cannot return readonly[{a.wrapped}] "
            f"{self.readonly_return_hint(e, a.wrapped)}", loc)

    def readonly_return_hint(self, slot: TpyType, returned: TpyType,
                             fix: TpyType | None = None) -> str:
        """Where a readonly borrow of `returned` was returned and how to
        declare that part of the result: `readonly[...]` on it (`fix`, the
        whole `slot` by default), or, in the const clone of an
        `@auto_readonly` def, an `auto_readonly[...]` marker on it in the
        result as the user declared it -- never the clone's own projection
        of that result."""
        fn = self.ctx.func.current_function
        if getattr(fn, "auto_readonly_polarity", None) == "apply" and fn.marked_return_type is not None:
            return (f"at a mutable part of return type '{fn.marked_return_type}' of an "
                    f"@auto_readonly method; mark that part 'auto_readonly[{returned}]'")
        return (f"at mutable return type '{slot}'; declare the return as "
                f"'{fix if fix is not None else ReadonlyType(slot)}'")

    def check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: CoercionContext | None = None,
        target_is_storage_form: bool = False,
        landing: 'UnionLanding | None' = None,
        *,
        param: str | None = None,
    ) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Raises SemanticError on incompatible types.
        Returns a Coercion if a conversion should be applied at codegen time,
        or None if compatible with no coercion needed. `landing` receives
        the union member the check landed the value in.

        A slot step that does not commit (`SemanticContext.slot_step_commits`)
        asks it as a query, as does a caller that opens a `ProbeSlot.QUERY`
        scope: the check decides the type only and writes nothing -- no copy
        warning or declaration, no owning sink, no pending settlement, no
        list literal decided -- so a candidate can ask it and lose without
        leaving a trace. The query runs the check the committed slot runs,
        its refusals included; only its writes wait.
        """
        if self.ctx.slot_step_commits():
            return self._check_type_compatible(
                actual, expected, context, loc, source_expr, is_return,
                coercion_ctx, target_is_storage_form, landing, param=param)
        with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
            return self._check_type_compatible(
                actual, expected, context, loc, source_expr, is_return,
                coercion_ctx, target_is_storage_form, landing, param=param)

    def _check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None, source_expr: TpyExpr | None,
        is_return: bool, coercion_ctx: CoercionContext | None,
        target_is_storage_form: bool,
        landing: 'UnionLanding | None' = None,
        *,
        param: str | None = None,
    ) -> Optional[Coercion]:
        """`check_type_compatible`, its writes made only where the slot
        step commits (`SemanticContext.slot_step_commits`)."""
        # Phase 20 Stage 5a: reject the slicing shape -- storing a borrow
        # of a polymorphic class into an owned slot of the same type would
        # silently drop the dynamic type. Fires before _check_compat so the
        # user sees a targeted "use Box[Throwable]" diagnostic rather than
        # a generic type mismatch.
        self._check_polymorphic_slicing(actual, expected, source_expr, context, loc)
        pending = self._pending_num_ends(actual, expected, source_expr, context)
        if pending is not None:
            if pending and self.ctx.slot_step_commits():
                self.pend.check(strip_int(actual), strip_int(expected), context,
                                coercion_ctx, source_expr)
            return None
        actual = self.pend.current(actual)
        # A dict or set slot of a call resolved against a container whose
        # leaves have settled names them too.
        expected = self.pend.current(expected)
        if is_return:
            self._check_readonly_return(
                self._narrowed_member_access(actual, source_expr), expected,
                loc)
        # A check that produces the coercion is where a list literal's
        # element is decided by the typed container it meets.
        result = self._check_compat(actual, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, landing=landing, param=param)
        if isinstance(result, CompatError):
            # A literal-seeded local takes its type from what is stored in
            # it, never from a use: say what declares the type the use wants.
            hint = (self.pend.annotation_hint(source_expr.name, expected)
                    if isinstance(source_expr, TpyName)
                    else self.pend.elem_annotation_hint(source_expr, expected,
                                                        coercion_ctx))
            if result.tuple_copy_remedy and self._sink_owns_its_value(
                    expected, coercion_ctx, target_is_storage_form):
                # The store refuses the readonly element as it refuses a
                # readonly scalar; a copy of the tuple is mutable throughout.
                src = (source_expr.name if isinstance(source_expr, TpyName)
                       else "...")
                hint += f" -- use copy({src}) to store a mutable copy"
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
        # Module-aware: resolves a cross-module recursive alias the caller
        # didn't import (so a container arg matches the alias's
        # list[Alias] / dict[_, Alias] members). See resolve_alias_ref.
        if (not isinstance(typ, AliasRef)
                and not self.ctx.recursive_union_names):
            return typ
        return self.ctx.registry.resolve_recursive_refs(typ)

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
        *,
        slot_readonly: bool = False,
        landing: 'UnionLanding | None' = None,
        param: str | None = None,
    ) -> CompatResult:
        """Core type compatibility check.

        Returns Coercion or None on success, CompatError on failure.

        Its writes -- a list literal's element decided by the typed
        container it meets (`_list_at_container`), owning sinks, copy
        warnings -- are made only where the slot step commits
        (`SemanticContext.slot_step_commits`): a check that only asks runs
        under a query (`ProbeSlot.QUERY`).

        target_is_storage_form: True when the destination is a field or
        container element (value-storage form). Used to suppress address-
        take mutation marking that only applies to borrow-form destinations
        (params/locals/returns); storage-form assignments copy the value.

        landing: where a committing check records the union member it
        landed the value in, handed down where the recursion
        peels a wrapper off the same value and slot.

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
                                          source_expr, coercion_ctx)
            if isinstance(met, CompatError):
                return met
            if met is not None:
                actual = met

        # A NAME feeding a slot that owns it must reach the Own[T] coercion
        # path even when the types are already equal: that path is where the
        # source's consumption is DECIDED -- the copy warning at a non-last-use
        # and, through `claim_name_move`, the hidden-borrow demotion that
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
                # An equal-type tuple source (a select of names, a call)
                # still lends what its slot's `Own` elements own.
                if isinstance(actual, TupleType):
                    self._check_own_element_slots(actual, expected,
                                                  source_expr)
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
                landing=landing, param=param)
        if isinstance(actual, (SendType, SyncType)):
            # Marker-typed value into an unmarked slot: the marker only adds
            # a guarantee, so it converts freely to the bare type.
            return self._check_compat(
                actual.wrapped, expected, context, loc, source_expr,
                is_return, coercion_ctx, target_is_storage_form, sink_owns,
                landing=landing, param=param)
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
                actual, expected, loc)
            if resolved is not None:
                if self.ctx.slot_step_commits() and source_expr is not None:
                    self.ctx.assert_slot_effect(
                        "a resolved pending generic instance's type")
                    self.ctx.set_expr_type(source_expr, resolved)
                return self._check_compat(
                    resolved, expected, context, loc, source_expr,
                    is_return, coercion_ctx, target_is_storage_form, sink_owns, param=param)
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
                slot_readonly=slot_readonly, landing=landing, param=param)
        # Strip Ref from actual too (Ref[T] is compatible with T)
        if isinstance(actual, RefType):
            return self._check_compat(
                actual.wrapped, expected, context, loc,
                source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns,
                slot_readonly=slot_readonly, landing=landing, param=param)

        # readonly[T] -> readonly[T]: unwrap and check inner types
        # T -> readonly[T]: always OK (adding const is safe)
        if isinstance(expected, ReadonlyType):
            actual_inner = unwrap_readonly(actual)
            if (is_readonly_ptr(actual_inner)
                    and not isinstance(unwrap_ref_type(expected.wrapped), PtrType)):
                # A readonly slot takes the pointee of a readonly pointer as
                # it would a mutable one's: it hands out no write either way.
                # A readonly POINTER slot (a field that cannot be re-pointed)
                # keeps the pointee's own access.
                actual_inner = PtrType(actual_inner.inner_pointee)
            return self._check_compat(
                actual_inner, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form,
                sink_owns, slot_readonly=True, landing=landing, param=param)

        # readonly[T] -> T: error for non-value types (stripping const is unsafe)
        # Exceptions: value types (copies), readonly protocols (Sized,
        # Sequence). A return slot's own readonly is decided once, at the top
        # (`check_type_compatible`); a return's tuple elements are the tuple
        # capture's, which owns their message.
        # Note: mutable Span is NOT excepted even for returns -- you cannot
        # construct a mutable span from a const container.
        if isinstance(actual, ReadonlyType) and not isinstance(expected, ReadonlyType):
            if (isinstance(actual.wrapped, TupleType) and not is_return
                    and not isinstance(expected, OwnType)):
                # A tuple copies its elements, so the copy is mutable only
                # where an element is: readonly projects onto each one, as
                # the literal `(t[0], k)` judges them. An `Own[...]` slot
                # takes a copy of the whole value, as it takes a readonly
                # scalar (the value-type rule below).
                result = self._check_compat(
                    TupleType(tuple(make_readonly(e)
                                    for e in actual.wrapped.element_types)),
                    expected, context, loc, source_expr, is_return, coercion_ctx,
                    target_is_storage_form, sink_owns, landing=landing, param=param)
                if isinstance(result, CompatError) and result.copy_admits:
                    result.tuple_copy_remedy = True
                return result
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
                        loc, copy_admits=not (is_return or is_mutable_span),
                    )
            return self._check_compat(
                actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form,
                sink_owns, landing=landing, param=param)

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
            if not self.ctx.slot_step_commits():
                return INTO_ANY
            # `Any` owns its contents, so it is an owning slot of the
            # concrete payload it erases.
            self.register_sink(source_expr, inner, TupleSink.ANY)
            if (source_expr is not None
                    and self.is_lvalue(source_expr)
                    and not self.is_copy_call(source_expr)
                    and self._warns_copy_into_any(inner)
                    and not self.claim_name_move(
                        source_expr, OwnType(inner),
                        sink=SinkCopy(inner, inner, "Any"))):
                self.register_sink_copy(SinkCopy(inner, inner, "Any"),
                                        source_expr)
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
                result = self._check_compat(member, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, param=param)
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
                    landing=landing, param=param)
            for member in _union_member_order(actual_unwrapped, union_members):
                with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
                    result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, slot_readonly=slot_readonly, param=param)
                if (isinstance(result, Coercion) and result.narrows_checked
                        and is_fixed_int_type(unwrap_own(actual))):
                    # A union stores a fixed-width value as one of its
                    # alternatives, never through a range-checked conversion
                    # into a member (an `int` keeps its checked one).
                    continue
                if not isinstance(result, CompatError):
                    if self.ctx.slot_step_commits():
                        # The member that admits the value is the slot it
                        # goes to: what the check writes about the value is
                        # written against that member, not against one
                        # tried and refused.
                        result = self._check_compat(actual, member, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, slot_readonly=slot_readonly, param=param)
                        if landing is not None:
                            landing.member = member
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
            return CompatError(self.mismatch_text(context, [expected], actual), loc)

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
                    and not target_is_storage_form
                    and self.ctx.slot_step_commits()):
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
                    source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, param=param)
                if inner_result is None:
                    return None
            result = self._check_compat(actual_inner, expected.inner, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, slot_readonly=slot_readonly, param=param)
            # Rewrap inner-mismatch errors with the declared Optional types so
            # the diagnostic reads `expected str | None, got StrView | None`
            # rather than the truncated `expected str, got StrView | None`.
            if isinstance(result, CompatError) and isinstance(actual, OptionalType):
                return CompatError(self.mismatch_text(context, [expected], actual), loc)
            return result

        # Optional[T] -> Optional[T] already handled by == check above
        # Optional[T] -> T: error (cannot implicitly unwrap)

        # T | None (pointer-repr) -> Ptr[T]: byte-identical T* at C++ level.
        # Per-context idiom: T | None for nullable returns, Ptr[T] for storage.
        # Storage-form Optional sources (field access, container element) need
        # an `optional_to_ptr` lift at codegen, mirroring the existing
        # storage-form-to-borrow-form lift for OptionalType destinations.
        if isinstance(expected, PtrType):
            actual_for_opt = pointer_repr_optional(unwrap_own(actual))
            if actual_for_opt is not None:
                # A readonly Optional's pointee is readonly too.
                actual_inner = (make_readonly(actual_for_opt.inner)
                                if readonly_access(actual) else actual_for_opt.inner)
                if _inner_compat_with_ro_widening(actual_inner, expected.pointee):
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
                        and not self.is_copy_call(source_expr)
                        and self.ctx.slot_step_commits()):
                    is_lvalue_src = self.is_lvalue(source_expr)
                    # Suppress at last use: no observable semantic divergence from
                    # CPython when the source is dead after this point (Framing A).
                    # A dead HANDLE moves only itself: the elements it hands
                    # out still live in the storage it lends from.
                    handle_lends = (self._dead_handle_value(source_expr)
                                    if isinstance(source_expr, TpyName)
                                    else None)
                    # A container that moves in skips each element copy; a
                    # later borrowing binding of the name settles the move
                    # into those copies (a handle's or value elements'
                    # settled copy is silent, as a read after it is).
                    skipped = () if handle_lends is not None else tuple(
                        SinkCopy(unwrap_ref_type(t.wrapped),
                                 unwrap_ref_type(t.wrapped), "owned storage",
                                 own_copy.KIND_ELEMENTS,
                                 ITERABLE_LVALUE_COPY_HINT)
                        for t in expected.inner_types()
                        if isinstance(t, OwnType)
                        and own_copy.contains_reference_type(t.wrapped))
                    is_auto_moved = self.claim_name_move(
                        source_expr, OwnType(expected), sink=skipped)
                    lent_value = handle_lends if is_auto_moved else None
                    prov_src = source_expr
                    # The copies a container makes of its elements are the
                    # name's (they move with it); a handle's -- a dead
                    # adapter, a generator object -- are of the elements it
                    # lends, whatever its own verdict.
                    copied_src: TpyExpr | None = (
                        None if holds_generator_object(
                            self.ctx.get_expr_type(source_expr))
                        else source_expr)
                    if lent_value is not None:
                        prov_src, is_lvalue_src = lent_value, False
                        copied_src = None
                    if isinstance(copied_src, TpyName):
                        # A container name's element copies are its own:
                        # they stand iff its record copies it.
                        self.register_sink(copied_src, OwnType(expected),
                                           None)
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
                                hint = ITERABLE_LVALUE_COPY_HINT
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
                                        self.ctx.assert_slot_effect(
                                            "an iterator copy check")
                                        self.pending_iter_copy_checks.append(
                                            PendingIterCopyCheck(
                                                prov_src, elem_type, display_type,
                                                hint, self.ctx._resolve_loc(source_expr),
                                                copied_src))
                                        continue
                                elif not iteration_copies_lent_reference(
                                        self.ctx, prov_src, elem_type):
                                    continue
                            if self.ctx.defer_own_copy_verdict(
                                    display_type, display_type,
                                    "owned storage", source_expr,
                                    kind=own_copy.KIND_ELEMENTS, hint=hint,
                                    source=copied_src):
                                continue
                            self._element_copy(
                                elem_type, display_type, hint,
                                self.ctx._resolve_loc(source_expr),
                                copied_src)
                return None
            if is_protocol_type(expected):
                return CompatError(
                    self.protocol_mismatch_text(actual_inner, stripped_expected, context),
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
                self.protocol_mismatch_text(check_actual, expected, context),
                loc
            )

        # Callable object -> Fn/Callable: record with a __call__ overload
        # satisfying the expected signature (first declared match wins).
        # Plain signature-to-signature matching, not `CallRanking.resolve` --
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
        if isinstance(actual, NominalType) and isinstance(expected, NominalType):
            if self.ctx.registry.is_subclass_of(actual, expected):
                msg = None
                if (coercion_ctx in (CoercionContext.ASSIGN,
                                     CoercionContext.INIT,
                                     CoercionContext.RETURN)
                        and self.ctx.slot_step_commits()):
                    msg = (f"upcast narrows '{actual}' to '{expected}' -- "
                           f"only '{expected}' fields and methods will be accessible; "
                           f"keep the concrete type or make '{expected}' a @dynamic protocol")
                elif coercion_ctx == CoercionContext.ARG:
                    msg = self._lost_override_arg_msg(actual, expected, param)
                if msg is not None:
                    self.ctx.assert_slot_effect("an upcast warning")
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
                        if coercion_ctx == CoercionContext.ARG:
                            self.warn_lost_override_arg(
                                actual.inner_pointee, expected.inner_pointee,
                                param, source_expr, loc)
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
                and same_class(actual, expected)
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
            # A return's move is the return check's to record; a hidden
            # borrower's demotion is applied here, where the return's
            # verdict has always been taken (a lambda's return has no other
            # check), retracting the last-use mark the lowering reads.
            # The ownership verdict -- move or copy, the copy's warning --
            # is the committed slot's; a query decides the type only.
            warned_ptr_repr_tuple = False
            ew = expected.wrapped
            if self.ctx.slot_step_commits():
                binding = self.ctx.comp_var_reads.get(source_expr)
                if is_return:
                    is_auto_moved = False
                    self.retract_hidden_borrowed_last_use(source_expr)
                elif binding is not None:
                    # A comprehension variable's move is its clause's
                    # (`comp_moves`, the record's); sema claims none, and the
                    # name-keyed ownership fact belongs to an outer local.
                    is_auto_moved = False
                else:
                    is_auto_moved = self.claim_name_move(
                        source_expr, expected, context)
                self.check_own_consumption(source_expr)
                # A loop variable moved out makes its loop consuming; a
                # comprehension variable is not the enclosing `for` variable.
                if (isinstance(source_expr, TpyName) and binding is None
                        and self.claim_loop_var_move(source_expr)):
                    self.ctx.mark_loop_var_consumed(source_expr.name)
                # A value-tuple with pointer-repr (reference) members copies each
                # member its source lends into owned storage where CPython
                # aliases, even though the tuple itself is a value type.
                self.register_sink(source_expr, expected, None)
                # A tuple source is checked per reference leaf
                # (`check_owned_tuple_sink`), a union or an open `T` leaf
                # included, a name's as any other's.
                ptr_repr_tuple = (isinstance(ew, TupleType)
                                  and bool(reference_paths(ew)))
                ref_scalar = not ew.is_value_type() and not self._is_value_type_param(ew)
                # A call that hands back a borrow is copied into the owning slot
                # exactly as a name or a field is, so it belongs to the same
                # family. An open type-param payload is no exception: the copy is
                # the same one the monomorphic twin makes, and `copy()` is
                # spellable there now that it takes a readonly source.
                # An owned element's pass was decided (moved, or its copy
                # warned) by the argument check, which runs first.
                element_decided = (isinstance(source_expr, TpySubscript)
                                   and self.element_read(source_expr)
                                   is not None)
                # A returned NAME's elements are its own return check's, and
                # so are a yielded tuple NAME's (`check_name_borrow_into_own`).
                returned_name = isinstance(peel_value_wrappers(source_expr),
                                           TpyName) and (
                    is_return or (coercion_ctx is CoercionContext.RETURN
                                  and bare_tuple(self.ctx.get_expr_type(
                                      source_expr)) is not None))
                if (ptr_repr_tuple and not element_decided and not returned_name
                        and source_expr is not None and not is_auto_moved):
                    self.check_owned_tuple_sink(
                        source_expr, expected, "owned storage", source_expr)
                    warned_ptr_repr_tuple = True
                elif (not element_decided and not is_return
                        and not returned_name and ref_scalar
                        and self.source_copies_into_storage(source_expr,
                                                            is_auto_moved)):
                    value_type = self.diag_type(self.ctx.get_expr_type(source_expr))
                    deferred = self.ctx.defer_own_copy_verdict(
                        value_type, expected.wrapped, "owned storage", source_expr,
                        source=source_expr)
                    note = self.binding_note(source_expr)
                    copied = self.pend.list_cells(
                        self.ctx.get_expr_type(source_expr))
                    remedy = self.copy_remedy(source_expr, note="")
                    if not deferred and self.ctx.is_type_non_copyable(
                            expected.wrapped):
                        hint = self.nocopy_hint(source_expr, note="")
                        noted = not self.is_consuming_field_borrow(source_expr)
                        target = expected.wrapped
                        if value_type == target:
                            head = (f"cannot copy non-copyable type '{target}' "
                                    f"into owned storage{hint}")
                        else:
                            head = (f"cannot copy {value_type} into owned storage "
                                    f"of type '{target}'; target is "
                                    f"non-copyable{hint}")
                        self.ctx.copy_error(
                            lambda n: head + (n if noted else ""), source_expr,
                            source_expr, note=note)
                    elif not deferred and copied is not None and not copied.settled:
                        # The copied list's element is decided later: the
                        # warning names it as it ends up.
                        list_t = self.ctx.get_expr_type(source_expr)
                        self.pend.defer(
                            source_expr, (copied.tree,),
                            lambda ts: self.ctx.copy_warning(
                                lambda n: f"copies "
                                f"{self.diag_type(with_container(list_t, ts[0]))} "
                                f"into owned storage; {remedy}{n}", source_expr,
                                source_expr, note=note))
                    elif not deferred:
                        self.ctx.copy_warning(
                            lambda n: f"copies {unwrap_readonly(value_type)} into "
                            f"owned storage; {remedy}{n}", source_expr,
                            source_expr, note=note)
            if warned_ptr_repr_tuple:
                return self._check_compat(actual, ew, context, loc,
                                          source_expr, is_return,
                                          coercion_ctx, target_is_storage_form, sink_owns,
                                          landing=landing, param=param)
            # Subclass coercion excluded: Child -> Own[Base] stores Child by value as
            # Base, silently slicing the object. Same invariance as container elements.
            # Only applies to a strict subclass, not parametric covariance.
            if self.ctx.registry.is_subclass_of(actual, expected.wrapped):
                return CompatError(
                    f"Type mismatch in {context}: expected {expected.wrapped}, got {actual}", loc)
            return self._check_compat(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, landing=landing, param=param)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self._check_compat(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx, target_is_storage_form, sink_owns, landing=landing, param=param)

        # Tuple-to-tuple: same length, element-wise compatible
        if isinstance(actual, TupleType) and isinstance(expected, TupleType):
            if len(actual.element_types) != len(expected.element_types):
                e, a = self._pair(expected, actual)
                return CompatError(
                    f"Type mismatch in {context}: expected {e}, got {a} "
                    f"(different tuple lengths)", loc
                )
            decided = self._check_own_element_slots(actual, expected,
                                                    source_expr)
            # Each element is checked against its own source: a literal's
            # member, else the tuple value itself.
            members = (source_expr.elements
                       if isinstance(source_expr, TpyTupleLiteral) else None)
            for i, (a, e) in enumerate(zip(actual.element_types, expected.element_types)):
                if members is None:
                    a_in = unwrap_own(unwrap_readonly(unwrap_send_sync(a)))
                    e_in = unwrap_own(unwrap_readonly(unwrap_send_sync(e)))
                    row = resolve_coercion(a_in, e_in, CoercionContext.ASSIGN)
                    if row is not None and row.narrows_checked:
                        # A tuple value converts as a whole, which has no
                        # per-element range check
                        # (BUGS.md#tuple-value-element-narrowing).
                        parts = ", ".join(f"{t}(...)"
                                          for t in expected.element_types)
                        return CompatError(
                            f"Type mismatch in {context}: a tuple element is "
                            f"{a_in}, which {e_in} does not hold every value "
                            f"of; convert the elements: ({parts})", loc)
                sub = members[i] if members is not None else source_expr
                # An `Own[T] | None` element is the owning slot of `T | None`,
                # checked as an `Own[T]` element is.
                if isinstance(e, OptionalType) and unwrap_optional_own(e):
                    e = OwnType(drop_own_marker(e))
                # A tuple ELEMENT is an owning storage slot whatever position
                # the tuple itself sits at: the tuple holds its elements by
                # value, so a borrowing tuple parameter still owns each one.
                # Forwarding the parent's flag judged a buffer element at a
                # tuple ARGUMENT to be borrowing and let a bytearray reach a
                # `bytes` element with no diagnostic at all.
                result = self._check_compat(
                    a, e, _tuple_element_context(context, i), loc,
                    source_expr=None if i in decided else sub,
                    is_return=is_return,
                    coercion_ctx=coercion_ctx, target_is_storage_form=True,
                    sink_owns=True, param=param)
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
                if coercion_refuses_value(actual, expected, coercion_ctx):
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
            def demote(a: TpyType, b: TpyType) -> None:
                self.ctx.assert_slot_effect("a pending list pair's demotion")
                self._demote_pending_pair(a, b)
            if unify_literal_types(
                    expected, actual,
                    on_pending_pair=(demote if self.ctx.slot_step_commits()
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
                if self.ctx.registry.is_subclass_of(actual_elem, e_elem):
                    # Explicit error: avoid leaking PendingList internal repr in the generic message.
                    return CompatError(self.mismatch_text(context, [e_elem], actual_elem), loc)
                else:
                    # Element type widening (e.g. int32 -> int32|None, int32 -> int64).
                    # Container element slot is storage form -- no address-take
                    # mark should fire even if the inner type is pointer-repr Optional.
                    result = self._check_compat(
                        actual_elem, e_elem,
                        context, loc, source_expr, is_return, coercion_ctx,
                        target_is_storage_form=True, sink_owns=True, param=param)
                    if not isinstance(result, CompatError):
                        return None  # element coercion is a probe, not propagated
                    # Definite mismatch: report it cleanly (see both_records above).
                    return CompatError(self.mismatch_text(context, [e_elem], actual_elem), loc)
            # Compatible with Array[T, N] if element types and sizes match
            if is_array(expected):
                if self.type_ops and self.type_ops.pending_list_matches_array(
                        actual, expected):
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
            source = literal_conversion_source(actual, expected)
            if source is not actual:
                coercion = resolve_coercion(source, expected, coercion_ctx,
                                            sink_owns)
                if coercion is None:
                    # A narrower fixed int: the value's own row when every
                    # value of the set fits, else checked as its storage type.
                    coercion = resolve_coercion(actual, expected,
                                                coercion_ctx, sink_owns)
                if coercion is not None:
                    return coercion
                actual = source
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
                and (actual.is_record or is_dyn_protocol(actual))
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
                if coercion is not None and ctx == CoercionContext.ARG:
                    self.warn_lost_override_arg(actual, expected.inner_pointee,
                                                param, source_expr, loc)
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
                if (deref_target is None and is_readonly_ptr(actual)
                        and ctx != CoercionContext.ARG and expected.is_value_type()):
                    # Ptr[readonly[T]] -> T derefs only where the slot COPIES
                    # the pointee (a value type); a reference slot would hand
                    # out the readonly pointee mutably.
                    deref_target = self.type_ops.get_deref_target_type(actual)
                if deref_target is not None and unwrap_readonly(deref_target) == expected:
                    # The slot binds the target itself, so a readonly target
                    # meets the readonly rule a readonly value would.
                    ro_err = None
                    if (isinstance(deref_target, ReadonlyType)
                            and not slot_readonly):
                        with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
                            ro_err = self._check_compat(
                                deref_target, expected, context, loc, None,
                                is_return, coercion_ctx,
                                target_is_storage_form, sink_owns, param=param)
                    if ro_err is not None:
                        return ro_err
                    coercion = DEREF_COERCION
        if coercion is None:
            return CompatError(
                self.mismatch_text(context, [expected], actual)
                + self._conversion_hint(actual, expected, ctx, sink_owns),
                loc)

        if self.ctx.slot_step_commits() and isinstance(actual, PendingListType) and is_span(expected):
            info = self.ctx.list_literal(actual.literal_id)
            if info:
                self.ctx.assert_slot_effect("a list literal passed to a Span")
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
            # The address-taking coercion's T& binding is marked where the
            # coercion is applied (`coerce_expr` -> `_mark_addr_taken`).
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
                    and source_expr is not None
                    and self.ctx.slot_step_commits()):
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

    def site_coercion(self, coercion: Coercion, actual: TpyType,
                      expected: TpyType, expr: TpyExpr,
                      value_range: 'ValueRange | None | object' = _READ_NOW,
                      inferred_slot: 'InferredSlot | None' = None,
                      ) -> Coercion:
        """`coercion` as it renders at this node: a checked integer
        narrowing renders as the slot's constructor call does for `expr`
        (`CallAnalyzer.narrowing_ctor`), its range check elided where the
        value range at the conversion site proves the value fits -- read
        now unless a conversion decided later passes the one it captured
        at its site. Any other row renders itself. A narrowing into a slot
        typed by an inferred type argument (`inferred_slot`) is refused: the
        user wrote no slot of that width."""
        if not coercion.narrows_checked:
            return coercion
        if inferred_slot is not None:
            raise self.ctx.error(
                f"Type mismatch in {inferred_slot.what}: expected "
                f"{int_payload(expected)}, got {actual}; the type argument of "
                f"'{inferred_slot.local}' was inferred as "
                f"{int_payload(expected)} from an earlier call, so annotate "
                f"its first binding (line {inferred_slot.line}) to give it "
                f"the wider type", expr)
        if value_range is _READ_NOW:
            value_range = site_value_range(self.ctx, expr)
        ctor = self.methods.calls.narrowing_ctor(
            expr, unwrap_own(unwrap_readonly(actual)), int_payload(expected),
            value_range)
        if ctor is None:
            return coercion
        # Local import: codegen_cpp imports sema.
        from ..codegen_cpp.context import expand_cpp_template
        template = ctor.cpp_template
        return dc_replace(
            coercion, context_free_wrap=True,
            codegen=lambda e, _a, _b, _c: expand_cpp_template(template, None,
                                                              e))

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
        inferred_slot: 'InferredSlot | None' = None,
        *,
        param: str | None = None,
    ) -> TpyExpr:
        """Wrap expr in a coercion node if a conversion is needed.

        target_is_storage_form: see _check_compat docstring. Set by callers
        that know the destination is a field / container element so the
        pointer-repr-Optional address-take mark is suppressed.
        inferred_slot: the slot's type is a type argument an earlier call
        inferred (`site_coercion` refuses a narrowing into it).
        param: the parameter an argument lands in, as a diagnostic names it
        (`'a'`; `parameter 'object' of list.append` for a builtin's).

        A slot step that does not commit (`SemanticContext.slot_step_commits`)
        validates the conversion and hands `expr` back untouched: its
        committed slot places the coercion.
        """
        source = expr
        # The slot receives the callable a class-name factory stands for, so
        # the node the caller stores back is that lambda -- where it commits.
        expr = lambda_of(expr)
        commits = self.ctx.slot_step_commits()
        pending = self._pending_num_ends(actual, expected, expr, context)
        if pending is not None:
            if not (pending and commits):
                return expr if commits else source
            return self.pend.coerce(expr, strip_int(actual),
                                    strip_int(expected), context,
                                    coercion_ctx, inferred_slot=inferred_slot)
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
        landing = UnionLanding()
        coercion = self.check_type_compatible(
            actual, expected, context,
            getattr(expr, "loc", None),
            source_expr=expr,
            is_return=is_return,
            coercion_ctx=coercion_ctx,
            target_is_storage_form=target_is_storage_form,
            landing=landing,
            param=param,
        )
        if not commits:
            return source
        if coercion is None and coercion_ctx in (CoercionContext.RETURN,
                                                 CoercionContext.ARG):
            # A result or an argument erases a @dynamic source into its
            # owner here; the binding sinks erase at their own rows.
            scope = self.ctx.func.current_scope
            coercion = dyn_erasure_coercion(
                expr, expected,
                scope.lookup(expr.name)
                if isinstance(expr, TpyName) and scope is not None else None,
                self.ctx, handle=coercion_ctx is CoercionContext.ARG)
        if coercion is None:
            return expr
        # Value-to-mutable-Ptr coercions (`record_to_ptr`, `upcast_to_ptr`)
        # emit `&expr` and require the source storage to stay mutable -- same
        # propagation the explicit `take_ptr(x)` path gets via the
        # `value_ptr_coercion` handler in calls.py and the vararg pack handler.
        if (coercion.produces_non_null_ptr
                and coercion.requires_mutable_lvalue):
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
        bare_expected = unwrap_own(unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(expected))))
        union_member = (landing.member
                        if (coercion_ctx == CoercionContext.ARG
                            and isinstance(bare_expected, UnionType)
                            and bare_expected.is_value_type())
                        else None)
        actual = literal_conversion_source(actual, expected)
        self.ctx.assert_slot_effect("a coercion node")
        coercion = self.site_coercion(coercion, actual, expected, expr,
                                      inferred_slot=inferred_slot)
        coerced = TpyCoerce(
            expr=expr,
            actual_type=actual,
            expected_type=expected,
            coercion=coercion,
            context_kind=coercion_ctx,
            context_msg=context,
            runtime_bigint=runtime_bigint,
            by_step=True,
            union_member=union_member,
            loc=expr.loc
        )
        self.ctx.set_expr_type(coerced, expected)
        return coerced

    def _repointed_tuple_slot(self, name: str, var_type: TpyType,
                              value_expr: TpyExpr
                              ) -> 'tuple[TpyType, bool]':
        """The slot a reassignment's value is checked against, and whether
        its check against the declared slot waits for the final answer. A
        pointer tuple (`pointer_tuple_names`) bound to a value owning no
        element only re-points its element pointers, so nothing is copied
        into the declared `Own` positions: the value is checked against the
        borrow tuple, the declared type with the `Own` markers dropped. The
        answer only grows during the walk, so a name it leaves out before
        the body's final set exists takes the borrow tuple too, and the
        declared slot's check runs after the body if the name stays out."""
        bare = unwrap_readonly(var_type)
        if (self.ctx.is_top_level or not isinstance(bare, TupleType)
                or not any(isinstance(et, OwnType)
                           for et in bare.element_types)
                or any(tuple_elem_owned(self.ctx, value_expr, var_type)
                       or ())):
            return var_type, False
        f = self.ctx.func
        fn = f.current_function
        borrow = TupleType(tuple(unwrap_own(et) for et in bare.element_types))
        if name in pointer_tuple_names(
                self.ctx, fn, f.current_reassigned_vars,
                f.current_param_names | f.current_nonlocal_names):
            return borrow, False
        if fn is not None and fn not in self.ctx.pointer_tuples_by_func:
            return borrow, True
        return var_type, False

    def park_repointed_tuple_check(
            self, name: str, stmt: TpyVarDecl, value_expr: TpyExpr,
            value_type: TpyType, var_type: TpyType, context: str) -> None:
        """Park the declared slot's check of a rebind the final set may
        still leave out (`_repointed_tuple_slot`) with the state, scope,
        namespace and flow state of its site, which the check reads as the
        walk would have there; `finalize_pointer_tuples` runs it
        (`run_repointed_tuple_check`)."""
        assert isinstance(stmt, TpyVarDecl), stmt
        f = self.ctx.func
        # The read's hidden-borrow demotion is taken here, under the borrow
        # state of its site, as the walk takes it at any other read.
        if (isinstance(value_expr, TpyName)
                and value_expr in self.ctx.all_last_uses):
            self.demoted_by_hidden_borrow(value_expr)
        self.ctx.pending_pointer_tuple_rebinds.setdefault(
            f.current_function, []).append(ParkedRebind(
                ParkedFunctionState(f.current_function, f, f.current_scope,
                                    f.current_ns),
                name, stmt, value_expr, value_type, var_type, context,
                self.init.save()))

    def run_repointed_tuple_check(self, rebind: ParkedRebind) -> None:
        """The declared slot's check of a parked rebind, as the walk would
        have made it at the site; what it defers is drained here, under the
        body's end state, since the body's own drain has run."""
        with self.ctx.reenter(rebind.parked):
            end = self.init.save()
            self.init.restore(rebind.flow)
            try:
                rebind.stmt.init = self.coerce_expr(
                    rebind.value_expr, rebind.value_type, rebind.var_type,
                    rebind.context, coercion_ctx=CoercionContext.ASSIGN)
            finally:
                self.init.restore(end)
            self.drain_deferred_escape_checks()

    def warn_foreign_store_narrowing(self, name: str, value_type: TpyType,
                                     slot_type: TpyType,
                                     value_expr: TpyExpr | None,
                                     node: TpyExpr | TpyStmt) -> None:
        """A store from another body -- through `global` or `nonlocal` --
        into a variable whose type the owning scope inferred from its own
        bindings, of a value that narrows into that type: it converts with
        the run-time check a declared slot's store takes, where Python
        stores the wider value, so it warns. Annotating the first binding
        (a declared slot) or converting the value explicitly silences it. A
        value whose type its body decides later is judged then."""
        foreign = self.deduction.foreign_inferred_slot(name)
        if foreign is None:
            return
        owner, line = foreign.owner, foreign.line
        if pending_leaves(strip_int(value_type)):
            self.pend.defer(node, (value_type, slot_type),
                            lambda ts: self.warn_foreign_store_narrowing(
                                name, ts[0], ts[1], value_expr, node))
            return
        value = unwrap_own(unwrap_readonly(value_type))
        slot = unwrap_own(unwrap_readonly(slot_type))
        row = resolve_coercion(value, slot, CoercionContext.ASSIGN)
        if row is None or not row.narrows_checked:
            return
        rng = (site_value_range(self.ctx, value_expr)
               if value_expr is not None else None)
        if narrowing_proven_safe(value, slot, rng):
            return
        here = self.ctx.func.current_function
        where = (" from a generator expression"
                 if isinstance(here, TpyFunction) and here.is_genexpr
                 else f" from '{here.name}'" if isinstance(here, TpyFunction)
                 else "")
        at = f" (line {line})" if line is not None else ""
        slot_name = python_type_name(slot)
        wide = smallest_common_int(slot, value)
        widen = (f", `{name}: {python_type_name(wide)} = ...` widens it"
                 if wide is not None else "")
        self.ctx.warning(
            f"'{name}' was inferred as {slot_name} in {owner}; this store"
            f"{where} narrows {python_type_name(value)} into it and raises "
            f"OverflowError where Python stores the wider value; annotate "
            f"its first binding{at}: `{name}: {slot_name} = ...` keeps "
            f"it{widen}", node)

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
            # A store from another body (`nonlocal`, a generator expression's
            # walrus) outlives that body's own storage: only a static source
            # keeps the enclosing variable a view.
            foreign = (name in self.ctx.func.current_nonlocal_names
                       and self.deduction.has_nonstatic_view_source(value_expr))
            if vf.is_any_member(inner_value_owned):
                if foreign or not self.deduction.is_view_compatible_source(
                        value_expr, inner_value_owned):
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
        self.warn_foreign_store_narrowing(name, inner_value, var_type,
                                          value_expr, err_node)
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
            slot, undecided = self._repointed_tuple_slot(
                name, var_type, value_expr)
            coerced = self.coerce_expr(
                value_expr, inner_value, slot, ctx,
                coercion_ctx=CoercionContext.ASSIGN)
            if undecided:
                self.park_repointed_tuple_check(
                    name, err_node, value_expr, inner_value, var_type, ctx)
        # Readonly status flows from the value expression.
        if isinstance(value_type, ReadonlyType):
            if isinstance(var_type, OptionalType):
                if not var_type.is_value_type():
                    var_type = OptionalType(ReadonlyType(var_type.inner))
            else:
                var_type = make_readonly(var_type)
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
        with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
            fwd = self._check_compat(new_elem, existing_elem, context, loc,
                                     None, False, CoercionContext.ASSIGN,
                                     target_is_storage_form=True)
            if not isinstance(fwd, CompatError):
                return None
            bwd = self._check_compat(existing_elem, new_elem, context, loc,
                                     None, False, CoercionContext.ASSIGN,
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
        # wraps -- the getter's RETURN CONVENTION does, as the value
        # category reads it (`accessor_returns_ref`): a reference it hands
        # back is existing storage whatever the receiver.
        if accessor_returns_ref(self.ctx, expr):
            return True
        # Everything else (calls, literals, operators) are rvalues
        return False

    def is_const_ref_source(self, expr: TpyExpr) -> bool:
        """Check if expression provides a const reference (can't be captured as T&):
        a place readonly-typed itself or reached through readonly storage,
        this side of any `Ptr` / `Span` step (`const_place`)."""
        return const_place(expr, self.ctx.get_expr_type, lambda e, _stepped: isinstance(
            self.ctx.get_expr_type(e), ReadonlyType))

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
        (`_shown`)."""
        return self.ctx.type_pair(self._shown(expected), self._shown(actual))

    def _shown(self, t: TpyType) -> TpyType:
        """`t` as a mismatch message spells it: the user-facing type
        (`diag_type`), an unresolved int literal the Python `int`."""
        return resolve_int_literals(self.diag_type(t), BIGINT)

    def mismatch_text(self, context: str, expected: Sequence[TpyType],
                      actual: TpyType) -> str:
        """The `Type mismatch in <context>: expected E, got A` message of a
        slot that takes none of the value; a slot several declared types
        could fill (an overloaded subscript's key) names each, `expected
        E1 or E2`."""
        pairs = [self._pair(e, actual) for e in expected]
        plain = str(self._shown(actual))
        # A name another listed type shares is told apart in every pair.
        got = next((a for _, a in pairs if a != plain), plain)
        return (f"Type mismatch in {context}: expected "
                f"{' or '.join(e for e, _ in pairs)}, got {got}")

    def protocol_mismatch_text(self, actual: TpyType, protocol: TpyType,
                               context: str) -> str:
        """The message of a value of type `actual` that `protocol`
        does not take, both spelled as the user wrote them -- a literal
        still pending in either (a module-level list's element) included."""
        return (f"Type {self.call_type_text(actual)} does not conform to "
                f"protocol {self.call_type_text(protocol)} in {context}")

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

    def variant_text(self, name: str, variant: 'FunctionInfo') -> str:
        """One overload variant of `name` as a diagnostic lists it: its
        parameter types, `/` and `*` where it has them."""
        parts: list[str] = []
        params = variant.params
        for i, p in enumerate(params):
            if (p.keyword_only and not p.is_kwargs
                    and (i == 0 or not params[i - 1].keyword_only)
                    and not any(q.is_variadic for q in params[:i])):
                parts.append("*")
            text = self.call_type_text(p.type)
            parts.append(f"*{p.name}" if p.is_variadic
                         else f"**{p.name}" if p.is_kwargs
                         else f"{p.name}: {text}")
            if p.positional_only and (i + 1 == len(params)
                                      or not params[i + 1].positional_only):
                parts.append("/")
        return f"{name}({', '.join(parts)})"

    def ambiguous_overload_text(self, func_name: str,
                                candidates: tuple['FunctionInfo', ...]) -> str:
        """The message every call position reports a top tie with."""
        head = (f"Ambiguous overload for '{func_name}': "
                f"multiple candidates match equally:")
        return "\n".join([head] + [f"  {self.call_signature_text(c)}"
                                   for c in candidates])

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
        if isinstance(t, TupleType):
            # A literal-bound tuple's elements are still literal types.
            return elem(t)
        return t.map_inner_types(self.diag_type)

    def is_last_use_move_candidate(self, expr: 'TpyExpr | None') -> bool:
        """Whether this name read is a last use an owning sink would move:
        a last-use mark on an owned local/param whose storage has no
        borrower liveness cannot see (`_has_hidden_borrower`). A question
        only -- it never authorizes a move or suppresses a copy
        diagnostic, and retracts no mark: a sink that acts on the answer
        asks `claim_name_move`, which records the read and applies the
        demotion."""
        return (self._owned_last_use(expr)
                and not self._has_hidden_borrower(expr))

    def retract_hidden_borrowed_last_use(self,
                                         expr: 'TpyExpr | None') -> None:
        """The demotion `claim_name_move` applies, at a read whose move is
        no owning sink's (a plain bind's move-through, a loop's iterable, a
        return the return check records): a hidden borrower retracts the
        last-use mark, so the lowering, which moves at every mark, copies."""
        if self._owned_last_use(expr):
            self.demoted_by_hidden_borrow(expr)

    def move_before_alias_reads(self, read: TpyName) -> None:
        """Name `read` moves where C++ may sequence the move before an alias
        read of its object in the same statement (`same_stmt_alias_reads`),
        and a moved owner no longer shares its object with the alias: not a
        last use, so it settles as an alias read in a later statement
        does."""
        if read in self.ctx.same_stmt_alias_reads:
            self.ctx.assert_slot_effect("a last-use retraction")
            self.ctx.all_last_uses.discard(read)

    def _owned_last_use(self, expr: 'TpyExpr | None') -> TypeGuard[TpyName]:
        """A name read carrying the last-use mark of a name that owns its
        value (`_is_owned_var`)."""
        return (isinstance(expr, TpyName)
                and expr in self.ctx.all_last_uses
                and self._is_owned_var(expr.name))

    def claim_name_move(self, expr: 'TpyExpr | None',
                        slot: 'OwnType | None' = None,
                        context: str = "owned storage", action: str = "pass",
                        *, whole_slot: bool = True,
                        sink: 'SinkCopy | tuple[SinkCopy, ...] | None' = None,
                        read: 'OwningRead | None' = None,
                        registers: bool = True) -> bool:
        """Whether this owning-sink read of a name moves as far as the body
        has been read -- the stamp the walk's own bookkeeping takes (a
        consumption, a move-through binding, a capture mode); never a copy
        diagnostic's verdict. A read it grants registers the copy the sink
        would make (`register_own_source_copy`, a tuple read's per leaf)
        unless the caller registers it itself (`registers` False), which
        settlement emits iff the read's record copies -- a tuple name's
        copies are its tuple sink check's (`check_name_borrow_into_own`,
        `warn_name_tuple_copy`), one entry per leaf; a read it refuses is
        the caller's to register -- an owning sink's copy, or nothing for a
        bind that only moves through when it can. `read` when given is the
        settlement's own record of a granted read. A
        hidden borrower demotes the move here, retracting the last-use mark
        the lowering and the source record read. Once the body is settled
        (a check run after it) the read's settled verdict answers."""
        if self.ctx.func.settled:
            return self._settled_name_moves(expr, slot)
        moves = (self._owned_last_use(expr)
                 and not self.demoted_by_hidden_borrow(expr))
        if read is not None or slot is None:
            if moves and read is not None:
                self.record_owning_read(read)
            return moves
        if not moves:
            return False
        assert isinstance(expr, TpyName)
        bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            self.ctx.get_expr_type(expr))))
        if (registers and bare_tuple(unwrap_own(bare)) is None
                and self.records_slot(slot, expr)):
            # A value payload copies nothing CPython would share. The read
            # has a record whatever check took it (a binding's move-through
            # is no sink of its own): its copy stands iff the record copies.
            self.register_sink(expr, slot, None)
            self.register_own_source_copy(slot, expr, context, action,
                                          whole_slot=whole_slot, sink=sink)
        return True

    def claim_loop_var_move(self, expr: TpyName) -> bool:
        """Whether an owning sink's read of loop variable `expr` moves it
        out -- where a local's move claim would succeed (`claim_name_move`'s
        legs): the read is its last use and no hidden borrower bars it, the
        demotion retracting the mark as for a local. A loop variable owns
        its element only through a consuming iteration (`_is_owned_var`
        leaves it out), which this verdict is what selects; a refused read
        copies with the sink's warning, as the local's does."""
        return (expr.name in self.ctx.func.loop_vars
                and expr in self.ctx.all_last_uses
                and not self.demoted_by_hidden_borrow(expr))

    def _settled_name_moves(self, expr: 'TpyExpr | None',
                            slot: 'TpyType | None') -> bool:
        """A name read's verdict once its body is settled: its record's,
        every leaf the slot owns moving. A read no sink recorded (a check
        parked past settlement, `run_repointed_tuple_check`) asks the rule
        the record builder asks per leaf (`name_moves`) of the leaves the
        slot owns -- a tuple name at one element's slot of the leaves its
        binding owns. Its last-use mark is the walk's: a hidden borrower
        demoted it at the read, under the borrow state of the site."""
        rec = read_record(self.ctx, expr)
        if rec is not None:
            return bool(rec.moved) and not rec.copied
        binding = (self.ctx.read_bindings.get(expr)
                   if isinstance(expr, TpyName) else None)
        if binding is None:
            return False
        t = self.ctx.get_expr_type(expr)
        inner = unwrap_own(unwrap_ref_type(slot)) if slot is not None else None
        if bare_tuple(inner) is not None:
            paths = owned_slot_paths(inner) or reference_paths(inner)
        elif bare_tuple(t) is not None:
            paths = read_ownership(self.ctx, binding).owned_among(
                t, reference_paths(t))
            if not paths:
                return False
        else:
            paths = frozenset({()})
        return all(self.name_moves(expr, p) for p in paths)

    def name_moves(self, read: TpyName, path: ElemPath, *,
                   returned: bool = False,
                   deferred_return: bool = True) -> bool:
        """Whether an owning sink's read of name `read` MOVES its leaf at
        `path`: the binding the read resolved to owns the leaf
        (`read_ownership`, fact 1), and the read consumes it -- its final
        last use (`all_last_uses` once settled, a hidden borrower's
        demotion retracted; a `return` a `finally` defers is one), its
        clause's move (`comp_moves`) or a lambda's whole result of its
        parameter (`returned`: the lambda's body is its one read). A return
        read the body reads again (a `finally`, a closure that captured the
        name) is no last use, and a captured name is read in place: neither
        consumes. `deferred_return` False: a parameter's return under a
        `finally` consumes only where it is a real last use (a VALUE leaf,
        whose copy costs nothing observable, `copy_permission`)."""
        binding = self.ctx.read_bindings.get(read)
        if binding is None or binding.kind is ReadKind.CAPTURED:
            return False
        if not read_ownership(self.ctx, binding).owns(
                self.ctx.get_expr_type(read), (path,)):
            return False
        comp = self.ctx.comp_moves.get(read)
        return (read in self.ctx.all_last_uses
                or comp in (CompMove.LAST_SINK, CompMove.RECEIVER)
                or (returned and binding.kind is ReadKind.LAMBDA)
                or (returned and deferred_return
                    and binding.kind is ReadKind.PARAM
                    and self._return_consumes_param(read)))

    def _return_consumes_param(self, read: TpyName) -> bool:
        """A whole `return` of an ownership-transfer parameter the last-use
        mark withholds consumes it where its capture is deferred past a
        non-suspending `finally` (`finally_return_candidates`), unless the
        `finally` rebinds it or a closure still reads it: a suspending
        `finally`'s, or a rebinding one's, eager capture copies."""
        return (read in self.ctx.finally_return_candidates
                and read not in self.ctx.finally_rebound_returns
                and read.name not in self.ctx.func.closure_pinned)

    def record_owning_read(self, read: OwningRead) -> bool:
        """Keep a read whose verdict settles with the body's ownership fact
        (`settle_owning_reads`): a move an owning sink made out of a LOCAL
        on the fact as it stands at the read. A parameter's ownership is its
        type, decided here (False: nothing is kept)."""
        if not self.settles_body_local(read.name):
            return False
        self.ctx.assert_slot_effect("an owning read")
        self.ctx.func.owning_reads.append(read)
        return True

    def settles_body_local(self, name: str, *,
                           module: bool = False) -> bool:
        """Whether `name` is a local of the function body being analyzed,
        whose ownership settles once the body is read (a parameter's and
        the receiver's are their types'). `module`: the answer outside a
        function body -- a module binding is an owning sink of its own
        (`SinkRead.binds`), owning by its storage's layout. A parameter's
        rebind registers through its declared slot's checks, at the flow
        state of their site (the coercion's `Own` slot, the repointed
        tuple's `run_repointed_tuple_check`)."""
        if not self.ctx.in_function_body():
            return module
        func = self.ctx.func.current_function
        return not ((name == "self" and self.ctx.self_names_receiver())
                    or any(p == name for p, _ in func.params))

    def settle_owning_reads(self, body: list[TpyStmt]) -> None:
        """Once the body is read, the ownership fact is complete. Before
        its verdicts are written (`record_body_ownership`), the reads whose
        outcome is an input to them settle: a consuming loop over a local
        the fact calls non-owning iterates borrowing after all. After it,
        everything that reads the verdict: the reads that move out of a
        local on the walk's fact (a tuple's elements, a consuming receiver,
        `own_iter`, a finally-deferred return), the sources' records
        (`build_source_records`), and the body's copy diagnostics, each
        emitted iff the record leaf it speaks for copies
        (`project_copies`)."""
        fs = self.ctx.func
        reads, fs.owning_reads = fs.owning_reads, []
        for r in reads:
            if isinstance(r, ConsumingLoop) and not fs.owns_whole(r.name):
                r.revert()
        record_body_ownership(self.ctx, body)
        start = fs.copies_from
        before = len(self.ctx.diagnostics)
        for r in reads:
            if isinstance(r, OwnIterMove):
                self._settle_own_iter(r)
            elif isinstance(r, UnneededCopy):
                if self._unneeded_copy(r.node):
                    self._warn_unneeded_copy(r)
            elif isinstance(r, FinallyCapture):
                self._settle_finally_capture(r)
        # A settled advice takes its place among the body's diagnostics,
        # as do the copies the arms above register as the walk does.
        added = self.ctx.diagnostics[before:]
        del self.ctx.diagnostics[before:]
        copies = [r for r in reads + fs.owning_reads
                  if isinstance(r, PendingCopy)]
        fs.owning_reads = []
        fs.copies_from = None
        fs.settled = True
        self.build_source_records()
        self._publish_body_copy_arguments(body)
        for r in reads:
            if isinstance(r, UnpackClaim):
                self._check_unpack_claim(r)
        self.project_copies(copies, added,
                            before if start is None else start)

    def _check_unpack_claim(self, r: UnpackClaim) -> None:
        """An unpack consuming its tuple NAME has its source's record: the
        walk tagged its targets (`is_owned`) before the body settled, and
        the lowering holds the tags to the record
        (`unpack_claim_agrees`). The tags cannot be rewritten here: the
        targets' own ownership (fact 1) was read off them already."""
        assert read_record(self.ctx, r.node) is not None, (
            "unpack source without a record", r.name, r.node.loc)

    def project_copies(self, copies: list[PendingCopy],
                       added: list[Diagnostic], start: int) -> None:
        """Emit each copy diagnostic the body registered (`PendingCopy`) iff
        a leaf it speaks for copies (`_copy_stands`), and the advice its settlement `added`, each in
        source order among the body's own diagnostics (from index `start`,
        `insert_in_source_order`), a line's after those already there. The
        first error raises, after the warnings before it."""
        # Each with its line and its registration order: on one line the
        # walk's order stands.
        keyed = [(_diag_line(d), -1, d) for d in added]
        error: tuple[float, int, Diagnostic] | None = None
        for k, entry in enumerate(copies):
            if not self._copy_stands(entry):
                continue
            note = entry.note() if entry.note is not None else ""
            diag = self.ctx.copy_diagnostic(entry, note)
            key = (_diag_line(diag), k, diag)
            if entry.level is DiagnosticLevel.ERROR:
                if error is None or key[:2] < error[:2]:
                    error = key
                continue
            keyed.append(key)
        keyed.sort(key=lambda x: x[:2])
        diags = self.ctx.diagnostics
        start = min(start, len(diags))
        for line, k, diag in keyed:
            if error is None or (line, k) < error[:2]:
                insert_in_source_order(diags, diag, start)
        if error is not None:
            raise self.ctx.error_from_loc(error[2].message, error[2].loc)

    def _copy_stands(self, entry: PendingCopy) -> bool:
        """Whether copy `entry` is made: a leaf of its source's record it
        speaks for copies (`project_copies`), whatever the source -- a name,
        an element, a call, a field, a select. A copy whose source no sink
        records is no copy of a source's leaf (the elements an iterator
        hands out, a local bound from a lending call): the check that
        registered it made it."""
        read = (peel_coerce(entry.source) if entry.source is not None
                else None)
        rec = read_record(self.ctx, read) if read is not None else None
        if rec is not None:
            copies = rec.copies_at(entry.path)
            assert copies is not None, (
                "copy registered at a path its record holds no leaf at",
                entry.path, read.loc)
            return copies
        # No record: a copy no conversion leaf takes, which its check made
        # -- an iterator's element copy (`_element_copy`, the @nocopy error
        # at `consume_all(gen(ns))` included), a pending argument copy
        # (`resolve_pending_argument_copies`), a frame argument's
        # (`warn_value_call_binding`).
        return True

    def _settle_finally_capture(self, r: FinallyCapture) -> None:
        """A finally-deferred return of a local whose binding does not own
        every reference leaf it holds (`read_ownership`, fact 1) takes the
        eager capture a non-owning local's return takes: the return copies
        before the finally chain runs. The capture moves the local whole --
        the returned name, or the literal member at `r.path` -- so a tuple
        local any element of which is non-owning is no deferrable move
        either."""
        binding = (self.ctx.read_bindings.get(r.node)
                   if isinstance(r.node, TpyName) else None)
        t = self.ctx.get_expr_type(r.node)
        paths = reference_paths(t) if bare_tuple(t) is not None else {()}
        if r.owned_elements is not None:
            # A mixed result moves only the elements it owns out of the
            # local; the others it lends, so the local need not own them.
            paths = [p for p in paths if p[:1] and p[0] in r.owned_elements]
        if (binding is not None and binding.kind is not ReadKind.CAPTURED
                and read_ownership(self.ctx, binding).owns(t, paths)):
            return
        ret = r.ret
        if r.restored_last_use:
            # The eager capture reads the local before the chain, which may
            # still reach it: no move there.
            self.ctx.all_last_uses.discard(r.node)
        if r.path is None or r.path == ():
            ret.finally_deferred_capture = False
            ret.finally_deferred_name = None
            ret.finally_deferred_leaves = ()
        else:
            ret.finally_deferred_leaves = tuple(
                p for p in ret.finally_deferred_leaves if p != r.path)
            if not ret.finally_deferred_leaves:
                ret.finally_deferred_capture = False

    def check_own_iter_source(self, name: TpyName, call: TpyExpr) -> None:
        """`own_iter(name)` takes the list itself, so `name` must own it: a
        parameter that is not `Own[...]`, or a local bound to existing
        storage, has no list of its own to give, and no copy of it renders
        here yet (BUGS.md#own-iter-non-owning-source). A parameter's is its
        type, asked here; a local's is its binding's (fact 1), asked once
        the body is settled (`OwnIterMove`)."""
        func = self.ctx.func.current_function
        if not self.ctx.in_function_body():
            return
        ptype = next((t for p, t in func.params if p == name.name), None)
        if ptype is None:
            self.record_owning_read(OwnIterMove(name.name, name, call))
            return
        if func.takes_ownership_of(name.name, ptype):
            return
        self._reject_own_iter_source(
            name, call,
            f"'{name.name}' is a parameter that does not take ownership")

    def _settle_own_iter(self, r: OwnIterMove) -> None:
        """A local's `own_iter` source once its binding's ownership is
        settled (`check_own_iter_source`)."""
        line = self._nonowning_line(r.node)
        if self.read_owns(r.node) or line is None:
            return
        self._reject_own_iter_source(
            r.node, r.call,
            f"'{r.name}' is bound to existing storage at line {line}")

    def _reject_own_iter_source(self, name: TpyName, call: TpyExpr,
                                why: str) -> None:
        raise self.ctx.error(
            f"own_iter() takes ownership of '{name.name}', which does not "
            f"own its list ({why}); iterate it with a plain for-loop "
            f"instead", call)

    def _tuple_arity(self, expr: TpyExpr) -> 'int | None':
        tt = self.ctx.get_expr_type(expr)
        tt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt)))
              if tt is not None else None)
        return len(tt.element_types) if isinstance(tt, TupleType) else None

    def comp_elem_moves(self, gen: TpyComprehensionGenerator, elem: TpyExpr,
                        is_last_sink: bool = False, *,
                        binding: 'CompTarget | None' = None) -> bool:
        """Whether a comprehension sink is the consuming MOVE -- an owned-source
        bare-loop-var sink. The LAST-evaluated sink (list/set element, dict
        value) is structurally a last use: the loop var is rebound each
        iteration and every earlier read (filter, dict key) is sequenced before
        it, so it moves unconditionally. An EARLIER sink (the dict key) gates on
        `all_last_uses` -- it may not move if a later sink reads the var
        (`{node: node.id}` must not move the key). The decision is recorded on
        the read (`comp_moves`), which the lowering's last-sink move and the
        source record read, so the two cannot drift.

        `binding`: `elem` is a consuming receiver reading that clause's
        variable inside the sinks; it moves only as the variable's sole
        read there."""
        inner = elem
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if binding is not None:
            assert isinstance(inner, TpyName) and is_last_sink
            gens = binding.comp.generators
            reads = [r for sink in comp_sinks(binding.comp)
                     for r in comp_free_reads(sink) if r.name == inner.name]
            if not (self._clause_owns(gen, inner, inner.name, ())
                    and binding.clause == len(gens) - 1
                    and self.ctx.func.current_scope is not None
                    and binding.depth == self.ctx.func.current_scope.depth
                    and any(r is inner for r in reads)):
                return False
            if len(reads) != 1:
                return False
            self.ctx.comp_moves[inner] = CompMove.RECEIVER
            return True
        read = self.element_read(inner, values=True)
        if (is_last_sink and read is not None
                and self._clause_owns(gen, inner, read[0], (read[1],))):
            # An element of the variable at the last sink, as a statement's
            # last-use element read: the sink moves it (the read's record
            # moves that element of the variable).
            self.ctx.comp_moves[inner] = CompMove.LAST_SINK
            self.ctx.comp_moves[inner.obj] = CompMove.LAST_SINK
            return True
        if not (isinstance(inner, TpyName)
                and self._clause_owns(gen, inner, inner.name, ())
                and (is_last_sink or inner in self.ctx.all_last_uses)):
            return False
        self.ctx.comp_moves[inner] = (CompMove.LAST_SINK if is_last_sink
                                      else CompMove.LAST_USE)
        return True

    def _clause_owns(self, gen: TpyComprehensionGenerator, read: TpyExpr,
                     name: str, path: ElemPath) -> bool:
        """Whether binding `name` of clause `gen` owns what `read` reads of
        it at `path` (`()`: the whole binding): a head target the step
        hands its element to (`is_owned`, the last target of that name the
        one bound), or the clause variable every leaf under `path` of which
        the step hands over (`step_handover`)."""
        head = gen.head
        if head is not None:
            return path == () and name in owned_head_targets(head)
        if name != gen.var or not gen.step_handover:
            return False
        t = self.ctx.get_expr_type(read)
        return all(p in gen.step_handover for p in leaf_paths(t, path))

    def declare_receiver_copy(self, recv: TpyName) -> None:
        """The copy a consuming call (`self: Own[Self]`) makes of a receiver
        that does not own its value (a borrowed parameter, a non-owning
        local, a comprehension variable its clause does not move): the
        owning slot's warning, or its error for a non-copyable type."""
        t = self.ctx.get_expr_type(recv)
        t = unwrap_readonly(unwrap_ref_type(t)) if t is not None else None
        if isinstance(t, OwnType):
            t = unwrap_readonly(t.wrapped)
        if t is None:
            return
        self._check_own_borrowed_source(OwnType(t), recv,
                                        "the consuming receiver", "pass")

    def advise_unneeded_copy(self, call: TpyCall) -> bool | None:
        """`copy(name)` at an owning sink: the advice to drop the copy where
        the bare name would move out whole -- the copy's source is EXPLICIT
        where its binding owns every leaf it holds and the read is its last
        use, the move the bare name would make (`_unneeded_copy`), asked of
        a local once the body is settled. Returns the walk's view, for the
        consumption it stamps: None when the bare name would not move;
        False when it would but still copy the tuple elements its binding
        holds by reference; True otherwise."""
        inner = call.args[0]
        if (isinstance(inner, TpyName) and inner in self.ctx.all_last_uses
                and not self._has_hidden_borrower(inner)):
            read = UnneededCopy(inner.name, inner, call)
            if not self.record_owning_read(read) and self._unneeded_copy(
                    inner):
                self._warn_unneeded_copy(read)
        if not self.is_last_use_move_candidate(inner):
            return None
        return not self.copy_into_own_idxs(inner.name)

    def _unneeded_copy(self, read: TpyName) -> bool:
        """Whether the bare name `read` would move out every leaf a
        `copy(read)` copies: its binding owns them (fact 1) and the read is
        its last use."""
        binding = self.ctx.read_bindings.get(read)
        if binding is None or binding.kind is ReadKind.CAPTURED:
            return False
        t = self.ctx.get_expr_type(read)
        # A value leaf held by reference is copied by the bare read too.
        return (read in self.ctx.all_last_uses
                and read_ownership(self.ctx, binding).owns(t, leaf_paths(t)))

    def _warn_unneeded_copy(self, r: UnneededCopy) -> None:
        self.ctx.warning(
            f"unnecessary copy() -- '{r.name}' is at its last use and "
            f"would be moved automatically", r.call)

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
        if self._has_hidden_borrower(expr):
            self.ctx.assert_slot_effect("a last-use retraction")
            self.ctx.all_last_uses.discard(expr)
            return True
        return False

    def _has_hidden_borrower(self, expr: TpyName) -> bool:
        """Whether `expr`'s storage has a borrower the liveness alias maps
        cannot see: bind-based aliases (b = a, a = o.inner, n = xs[0]) are
        modeled by the liveness walk itself; call-result and iterator
        borrows are not, so any such borrower bars a move."""
        known = (self.ctx.func.current_alias_sources.keys()
                 | self.ctx.func.current_chain_alias_sources.keys())
        return self.ctx.func.borrow_tracker.has_borrowers_outside(
            expr.name, known)

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
            # A consumption mark for the unconsumed-`Own`-parameter warning
            # only: the move itself is the calling sink's record.
            if self.is_last_use_move_candidate(expr):
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
            # `name[i]` returned at the tuple's last use MOVES the element
            # the binding owns out (nothing reads the moved-from element;
            # the others stay where they are).
            moves = self._element_move(own_type, expr, context, action,
                                       returned=True)
            if moves is not None:
                return not moves
        if not self.arrives_borrowed(expr):
            return False
        return self._check_own_borrowed_source(
            own_type, expr, context, action, whole_slot=whole_slot)

    def passed_owned_element(self, own_type: OwnType, expr: TpyExpr) -> bool:
        """A reference element `name[i]` passed to an `Own[T]` parameter: an
        owned one MOVES out exactly where a returned one does
        (`check_own_lvalue_into_own`: the tuple's last use with no hidden
        borrower); otherwise -- a borrowed element, or an owned one the
        tuple outlives -- the slot copies it and says so, as it does a
        scalar, and a `@nocopy` element errors. False when `expr` is no
        such element read."""
        read = self.element_read(expr)
        if read is None:
            return False
        self._element_move(own_type, expr, f"tuple element {read[1]}",
                           "pass", returned=False)
        return True

    def _element_move(self, own_type: OwnType, expr: TpyExpr, context: str,
                      action: str, *, returned: bool) -> bool | None:
        """An element read `name[i]` at an `Own` slot (`returned`: a whole
        return's): its copy registered, which its record decides
        (`element_moves_out`); True where that verdict moves the element out
        -- for a parameter, whose ownership is its type, the stamp its
        consumption takes. None for no element read."""
        read = self.element_read(expr)
        if read is None:
            return None
        self.register_own_source_copy(own_type, expr, context, action,
                                      whole_slot=returned)
        self.demote_element_read(expr)
        moves = self.element_moves_out(expr)
        if moves:
            self.ctx.mark_own_element_consumed(read[0], read[1])
        return moves

    def demote_element_read(self, expr: 'TpyExpr | None') -> None:
        """The demotion an owning sink's element read `name[i]` takes where
        the walk reads it: a hidden borrower of the tuple retracts its
        last-use mark, which the element's move verdict reads
        (`element_moves_out`) whichever sink registered it."""
        read = peel_value_wrappers(expr) if expr is not None else None
        if (isinstance(read, TpySubscript) and isinstance(read.obj, TpyName)
                and read.obj in self.ctx.all_last_uses
                and self.element_read(read) is not None):
            self.demoted_by_hidden_borrow(read.obj)

    def element_moves_out(self, expr: TpyExpr) -> bool:
        """Whether element read `name[i]` at an owning sink moves the
        element out of its tuple: the tuple's read is its last use and the
        binding it resolved to owns every leaf of the element (fact 1)."""
        read = (self.element_read(expr) if isinstance(expr, TpySubscript)
                else None)
        if read is None:
            return False
        tup = expr.obj
        binding = self.ctx.read_bindings.get(tup)
        if (binding is None or binding.kind is ReadKind.CAPTURED
                or tup not in self.ctx.all_last_uses):
            return False
        t = self.ctx.get_expr_type(tup)
        need = ({p for p in reference_paths(t) if p[:1] == (read[1],)}
                or {(read[1],)})
        return read_ownership(self.ctx, binding).owns(t, need)

    def element_read(self, expr: TpyExpr, *, values: bool = False
                     ) -> 'tuple[str, int] | None':
        """`(name, i)` for `name[i]` with a constant index reading a
        reference element of a tuple parameter or local (any element with
        `values`: an owned `str` moves too); None for any other read.
        Whether an owning sink moves it out is its record's
        (`element_moves_out`)."""
        if not (isinstance(expr, TpySubscript) and isinstance(expr.obj, TpyName)):
            return None
        idx = const_tuple_index(expr.index)
        if idx is None:
            return None
        tt = self.ctx.get_expr_type(expr.obj)
        tt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt)))
              if tt is not None else None)
        if not isinstance(tt, TupleType):
            return None
        if idx < 0:
            idx += len(tt.element_types)
        if not 0 <= idx < len(tt.element_types):
            return None
        et = unwrap_readonly(tt.element_types[idx])
        if (not values and unwrap_optional_own(et) is None
                and unwrap_ref_type(et).is_value_type()):
            return None
        return expr.obj.name, idx

    def copy_into_own_idxs(self, name: str) -> frozenset[int]:
        """The elements of tuple local `name` an owning sink copies rather
        than moves: those its current binding holds by reference, and those
        any binding of it borrows (the ownership fact, so far)."""
        fs = self.ctx.func
        base = fs.bp_copy_into_own_idxs(name)
        scope = fs.current_scope
        slot = scope.lookup(name) if scope is not None else None
        bare = (unwrap_readonly(unwrap_ref_type(unwrap_own(
            unwrap_send_sync(slot)))) if slot is not None else None)
        if not isinstance(bare, TupleType):
            return base
        return base | fs.nonowning_elems(name, len(bare.element_types))

    def returned_captured_name(self, expr: TpyExpr) -> 'TpyName | None':
        """The NAME a `return` hands over although a closure of the body
        captures it -- the closure may still read it after the return, so
        the read consumes nothing and an owning slot copies it rather than
        moving. None otherwise, and for a return a `finally` defers: that
        one moves after the finally (it is marked a last use)."""
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
        if (isinstance(expr, TpyName)
                and expr.name in self.ctx.func.closure_pinned
                and expr not in self.ctx.all_last_uses):
            return expr
        return None

    def _check_own_borrowed_source(
        self, own_type: OwnType, expr: TpyExpr, context: str, action: str,
        *, whole_slot: bool = True,
        sink: 'SinkCopy | tuple[SinkCopy, ...] | None' = None,
    ) -> bool:
        """`check_own_lvalue_into_own`'s tail, once the source is known to be
        borrowed: it warns and copies -- unless the source is a value type, a
        movable Own local, or a consuming method's own field. A `@nocopy`
        payload errors, because there is no copy for the warning to
        describe. True when the slot copies. `sink` is the copy's text at a
        sink other than an `Own` slot (a field, a container, `Any`, an
        element copy), settled here for a move a later binding revoked.
        A comprehension variable's read (`comp_var_reads`) arrives with its
        clause's copy decided."""
        if self._own_source_copies_nothing(own_type, expr, context, action,
                                           whole_slot=whole_slot):
            return False
        inner = peel_coerce(expr)
        if (inner not in self.ctx.comp_var_reads and self.claim_name_move(
                expr, own_type, context, action,
                whole_slot=whole_slot, sink=sink)):
            self.moved_into_own(expr, context)
            return False
        return self._own_source_copy(own_type, expr, context, action,
                                     whole_slot=whole_slot, sink=sink)

    def _returned_own_name(self, own_type: OwnType, read: TpyName,
                           context: str, action: str) -> None:
        """A whole return of Own-typed name `read`: its copy registered,
        which its record decides (a binding a later one makes non-owning, a
        return the body reads again copy). A nested def's capture is not
        registered: its render moves an `Own[T | None]` one
        (BUGS.md#nested-def-captured-own-return-copies). The walk's move
        stamps a consumption; a body local the body owns whole relocates
        by C++'s own return, every other name as the slot's move does
        (`moved_into_own`)."""
        binding = self.ctx.read_bindings.get(read)
        if (binding is not None and binding.kind is not ReadKind.CAPTURED
                and bare_tuple(unwrap_ref_type(
                    self.ctx.get_raw_expr_type(read))) is None):
            self.register_own_source_copy(own_type, read, context, action)
        if self.claim_name_move(read, own_type, context, action,
                                registers=False):
            if self.ctx.func.owns_whole(read.name):
                self.check_own_consumption(read)
            else:
                self.moved_into_own(read, context)

    def moved_into_own(self, expr: TpyExpr, context: str) -> None:
        """A borrowed source an `Own` slot (`context`) granted the move of:
        it must be movable, and an `Own` parameter is consumed."""
        self.require_movable(expr, context)
        self.check_own_consumption(expr)

    def _own_source_copies_nothing(self, own_type: OwnType, expr: TpyExpr,
                                   context: str, action: str, *,
                                   whole_slot: bool) -> bool:
        """Whether an `Own` slot fed from borrowed `expr` copies nothing
        whatever the read's move: a value type; a whole tuple source the
        owned tuple sink check takes per leaf; a WHOLE returned `Own`-typed
        name, which C++ moves; a consuming method's own field."""
        # Value types are always safe -- copied, not aliased. OwnType.is_value_type
        # returns True (Own represents a moved value), so we have to peel Own
        # first to see whether the underlying T is genuinely a value type.
        # A value-shaped type HOLDING an open type parameter (`T | None`,
        # `tuple[T, int32]`) is not: the parameter may be a reference type,
        # which the copy duplicates as it would a bare `T`. (A concrete
        # held instance is the per-element check's.)
        inner = peel_coerce(expr)
        raw_type = self.ctx.get_raw_expr_type(inner)
        if raw_type is not None:
            unwrapped = unwrap_ref_type(raw_type)
            inner_after_own = unwrapped.wrapped if isinstance(unwrapped, OwnType) else unwrapped
            # A whole tuple source is the owned tuple sink check's, per leaf
            # (`check_owned_tuple_sink`), a plain name read's as any other's;
            # a walrus and a name read behind a wrapper keep the whole-tuple
            # hedge below.
            if (whole_slot
                    and isinstance(unwrap_readonly(inner_after_own), TupleType)
                    and (isinstance(inner, TpyName)
                         or self.per_leaf_source(inner))):
                return True
            if (inner_after_own.is_value_type()
                    and not (own_copy.type_param_names(inner_after_own)
                             and own_copy.contains_reference_type(
                                 inner_after_own))):
                return True
            # A WHOLE returned Own-typed name moves out (C++ moves a returned
            # local) where its record says so; one a closure captures copies,
            # declared below like any owning slot's. One ELEMENT of a tuple
            # literal takes the read's move like any other name -- `return
            # (b, b)` hands back two copies where CPython hands back one
            # object twice. An Own-typed rvalue (a call declaring an owned
            # result) is fresh; any other Own-typed read (an element of a
            # container the body borrows) moves only where its own move
            # verdict says so (`element_moves_out`, a consuming field), both
            # decided before this.
            if (whole_slot and action == "return"
                    and isinstance(unwrapped, OwnType)
                    and (is_rvalue_source(self.ctx, inner)
                         or (isinstance(inner, TpyName)
                             and inner not in self.ctx.comp_var_reads))
                    and self.returned_captured_name(expr) is None):
                if isinstance(inner, TpyName):
                    self._returned_own_name(own_type, inner, context, action)
                return True
        # A consuming method's field read that sema let move out of `self`.
        return isinstance(inner, TpyFieldAccess) and inner.consuming_move

    def _own_source_copy(
        self, own_type: OwnType, expr: TpyExpr, context: str, action: str,
        *, whole_slot: bool = True,
        sink: 'SinkCopy | tuple[SinkCopy, ...] | None' = None,
    ) -> bool:
        """The copy an `Own` slot (`context`) makes of borrowed `expr` that
        did not move, registered in `sink`'s words where given, else the
        slot's (`register_own_source_copy`). True: the slot copies."""
        self.register_own_source_copy(own_type, expr, context, action,
                                      whole_slot=whole_slot, sink=sink)
        return True

    def register_own_source_copy(
        self, own_type: OwnType, expr: TpyExpr, context: str, action: str,
        *, whole_slot: bool = True,
        sink: 'SinkCopy | tuple[SinkCopy, ...] | None' = None,
    ) -> None:
        """Register the copy diagnostic an `Own` slot (`context`) makes of
        `expr` where its record copies it (`SemanticContext.register_copy`):
        in `sink`'s words where given, else the slot's warning, the hedge
        of an open payload, or the located error of a non-copyable one.
        The walk registers it at every owning-sink read; the record
        decides."""
        if sink is not None:
            for one in sink if isinstance(sink, tuple) else (sink,):
                self.register_sink_copy(one, expr)
            return
        expr_type = self.ctx.get_expr_type(expr)
        # Non-copyable, not just @nocopy: a record with `__del__` has no copy
        # constructor either, so the copy the warning below would declare
        # could not be built.
        if (expr_type is not None
                and self.ctx.is_type_non_copyable(expr_type)):
            reason = (self.ctx.nocopy_reason(expr_type)
                      if self.ctx.is_type_nocopy(expr_type)
                      else f"non-copyable type '{unwrap_readonly(expr_type)}'")
            verb = "returned" if action == "return" else "passed"
            consuming_field = self.is_consuming_field_borrow(expr)
            owned_name = (isinstance(expr, TpyName)
                          and peel_coerce(expr) not in self.ctx.comp_var_reads)

            def nocopy_text(note: str) -> str:
                if owned_name and self.read_owns(expr):
                    return (f"{reason} is used after this point and cannot "
                            f"be moved into {context} Own[{own_type.wrapped}]"
                            f". Remove later uses or restructure the code.")
                tail = (f": {CONSUMING_FIELD_MOVE_NOTE}" if consuming_field
                        else ". Only the original owner can be moved at "
                        f"its last use{note}")
                return (f"{reason} cannot be {verb} as {context} "
                        f"Own[{own_type.wrapped}]{tail}.")
            self.ctx.copy_error(nocopy_text, expr, expr,
                                note=self.binding_note(expr))
            return
        # The owning slot copies and says so, exactly as the insert, the
        # `Own[T]` parameter and the `yield` do -- one owning-slot rule, one
        # text, and `copy(...)` silences it everywhere. The copy is an
        # acknowledged CPython divergence (CPython hands over the very
        # object), which is what the warning declares. A tuple element names
        # its element.
        where = "" if whole_slot else f" ({context})"
        payload = own_type.wrapped
        if self._is_value_type_param(payload):
            # A `T: ValueType` bound proves the copy, so there is nothing
            # to declare -- the same exemption the insert slot applies.
            return
        value_type = self.diag_type(self.ctx.get_expr_type(expr))
        # Whether this copies at all is the instantiation's answer, not
        # the body's, so an open payload defers to the discharge.
        if not self.ctx.defer_own_copy_verdict(
                value_type, payload, f"owned storage{where}", expr,
                source=expr):
            shown, remedy = unwrap_readonly(value_type), self.copy_remedy(
                expr, note="")
            self.ctx.copy_warning(
                lambda note: f"copies {shown} into owned storage{where}; "
                f"{remedy}{note}", expr, expr, note=self.binding_note(expr))

    def read_owns(self, expr: TpyExpr) -> bool:
        """Whether the binding name read `expr` resolved to owns what it
        holds (`read_ownership`, fact 1): asked at settlement."""
        binding = self.ctx.read_bindings.get(peel_coerce(expr))
        return (binding is not None and binding.kind is not ReadKind.CAPTURED
                and bool(read_ownership(self.ctx, binding)))

    def binding_note(self, expr: TpyExpr, index: 'int | None' = None, *,
              path: 'ElemPath | None' = None) -> Callable[[], str]:
        """The note on the binding of read `expr` (`ownership_note`), taken
        when its copy diagnostic is emitted."""
        return lambda: self.ownership_note(expr, index, path=path)

    def register_sink_copy(self, sink: SinkCopy, expr: TpyExpr, *,
                           nocopy_hint: 'str | None' = None) -> None:
        """Register the copy diagnostic of a sink other than an `Own` slot
        in its own words: the hedge of an open payload, the located error of
        a non-copyable one (`nocopy_hint` its remedy where given), else the
        warning."""
        if self._is_value_type_param(sink.target):
            return
        if self.ctx.defer_own_copy_verdict(sink.display, sink.target,
                                           sink.dest, expr, kind=sink.kind,
                                           hint=sink.hint, source=expr):
            return
        non_copyable = self.ctx.is_type_non_copyable(sink.target)
        display = self.diag_type(sink.display, sink.target)
        remedy = self.copy_remedy(expr, note="")
        hint = (nocopy_hint if nocopy_hint is not None
                else self.nocopy_hint(expr, note=""))
        # The generic remedy names the binding; the consuming field's and
        # an explicit one do not.
        noted = (nocopy_hint is None
                 and not self.is_consuming_field_borrow(expr))

        def text(note: str) -> str:
            return own_copy.slot_message(
                display, sink.target, sink.dest, sink.kind, sink.hint,
                non_copyable=non_copyable, remedy=remedy + note,
                nocopy_hint=hint + (note if noted else ""), note=note)[1]
        self.ctx.register_copy(
            text, expr, expr,
            level=(DiagnosticLevel.ERROR if non_copyable
                   else DiagnosticLevel.WARNING),
            note=self.binding_note(expr))

    def _is_value_type_param(self, typ: TpyType) -> bool:
        """Check if a TypeParamRef has a ValueType bound in the current context."""
        if not isinstance(typ, TypeParamRef):
            return False
        return is_value_type_bound(
            self.type_ops.get_type_param_bound(typ.name))

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
        if self._resolve_recursive_refs(elem) == union:
            return False
        with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
            return not isinstance(
                self._check_compat(elem, union, "deep-union leaf"),
                CompatError)

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
        if source_expr is not None and self.ctx.slot_step_commits():
            self.ctx.assert_slot_effect("a union-member type rewrite")
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
                and same_class(actual, expected)
                and actual.type_args and expected.type_args
                and actual.type_args != expected.type_args):
            return False
        record_info = self.ctx.registry.get_record_for_type(actual)
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
            # A record that matches a @dynamic protocol only structurally
            # has no C++ base for the wrapper to convert to.
            if (is_protocol_type(expected_arg)
                    and self.ctx.registry.get_record_for_type(actual_arg) is not None
                    and not self.protocols.converts_to_dynamic_base(
                        actual_arg, expected_arg)):
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
                if (origin_param is not None
                        and self.ctx.slot_step_commits()):
                    self.ctx.mark_representational_param(origin_param)
                return True
            if isinstance(bound, NominalType) and bound.is_record:
                if self._is_covariant_target(bound, parent):
                    if (origin_param is not None
                            and self.ctx.slot_step_commits()):
                        self.ctx.mark_representational_param(origin_param)
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
        with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
            if not all(self._check_compat(e, a, "param", loc) is None
                       for a, e in zip(actual_params, expected.param_types)):
                return False
            return (self._check_compat(actual_return, expected.return_type,
                                       "return", loc) is None
                    or isinstance(expected.return_type, VoidType))

    def _lost_override_arg_msg(self, actual: TpyType, expected: TpyType,
                               param: str | None) -> str | None:
        # A parameter keeps the object (it binds by reference or pointer),
        # so only a method the subclass overrides tells the static dispatch
        # through the base apart from Python's.
        if not self.ctx.slot_step_commits():
            return None
        lost = self.protocols.lost_override(actual, expected)
        if lost is None:
            return None
        base_def, sub_def = lost
        through = param if param else "the parameter"
        return (f"'{actual}' passed as '{expected}': calls through "
                f"{through} run '{base_def}', not '{sub_def}' "
                f"(static dispatch)")

    def warn_lost_override_arg(self, actual: TpyType, expected: TpyType,
                               param: str | None, source_expr: TpyExpr | None,
                               loc: SourceLocation | None) -> None:
        msg = self._lost_override_arg_msg(actual, expected, param)
        if msg is None:
            return
        self.ctx.assert_slot_effect("an upcast warning")
        if source_expr is not None:
            self.ctx.warning(msg, source_expr)
        else:
            self.ctx.warning_from_loc(msg, loc)

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
        # Class inheritance, any class (a builtin base included)
        return self.ctx.registry.is_subclass_of(child, parent)

    def is_local_shadow(self, name: str) -> bool:
        """Check if a name is bound in a local scope, shadowing a global:
        it resolves here, and not to the module slot
        (`SemanticContext.reads_module_slot`)."""
        scope = self.ctx.func.current_scope
        return (scope is not None and scope.lookup(name) is not None
                and not self.ctx.reads_module_slot(name))

    def write_binds_local(self, name: str) -> bool:
        """Whether a store into module global `name` from here binds a local
        of the function instead: Python's rule that a bare write in a
        function body makes a local, unless `global` declares the name or a
        binding of the function already shadows it."""
        return (not self.ctx.is_top_level
                and name not in self.ctx.func.global_declarations
                and name in self.ctx.global_scope.bindings
                and not self.is_local_shadow(name))

    def _is_owned_var(self, name: str) -> bool:
        """Whether `name` owns its value, so its last use at an owning sink
        moves it: a parameter that takes ownership (its declared type), or
        an owning local -- one no binding made refer to existing storage
        (the ownership fact, `FunctionTrackingState.owns_whole`), as far as
        the body has been read; `settle_owning_reads` revisits a move a
        later binding takes back."""
        # A consuming method (`self: Own[Self]`) owns its receiver: `self` is
        # this frame's value, so its last use relocates it exactly as an
        # `Own[T]` param does. Same ownership fact the `self.field` arm in
        # `_check_own_borrowed_source` reads, one level up -- `self` is not in
        # `func.params`, so the loop below never sees it.
        if name == "self" and self.ctx.in_consuming_method:
            return True
        func = self.ctx.func.current_function
        if self.ctx.reads_module_slot(name):
            # A module global outlives every last use one body sees: other
            # bodies read it later.
            return False
        if self.ctx.in_function_body():
            # Own[T] params, and owned-element tuple params (the ownership-
            # transfer `std::tuple<...>&&` ABI -- movable like a scalar Own[T]).
            for pname, ptype in func.params:
                if pname == name:
                    return func.takes_ownership_of(pname, ptype)
            fs = self.ctx.func
            if not fs.owns_whole(name):
                return False
            # A for-loop variable gets Own[T] from a consuming iteration's
            # element type; its movability is the consuming iteration's
            # (consumed_loop_vars), not this binding's.
            scope_type = fs.current_scope.lookup(name) if fs.current_scope else None
            if (scope_type and isinstance(scope_type, OwnType)
                    and name not in fs.loop_vars):
                return True
            # Otherwise owning when a binding made it an object of its own
            # (`note_owned_local`) -- the set the lowering's movable locals
            # are drawn from (`rebinds_keep_ownership`).
            return (name in fs.ever_owned_locals
                    and fs.rebinds_keep_ownership(name))
        # A local of the module's init code (a block's binding, a desugar
        # temp) bound by reference (a pointer slot, or a tuple of them)
        # points at storage it does not own, so its last use is no move.
        var_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
        if var_type:
            inner = var_type.wrapped if isinstance(var_type, OwnType) else var_type
            if global_binds_by_reference(inner):
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

    def _name_is_param_or_global(self, name: str) -> bool:
        """True if the name refers to caller-owned storage that outlives the call.

        The one name rule of the durability readers (`_place_durable`,
        `_leaves_param_derived`): the self / param / non-shadowed-global
        checks in one place.

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
        same question it did (`SemanticContext.reads_module_slot`).
        """
        return self.ctx.reads_module_slot(name)

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

    def _place_durable(self, leaf: Leaf, *, view_source: bool,
                       gen_yield: bool, strict: bool) -> bool:
        """The name rule at a PLACE leaf of the durability climb
        (`ReturnLend`): a parameter, `self` or a module global outlives the
        call (`_name_is_param_or_global`); a local bound from durable
        storage is `safe_to_return` -- unless the leaf is a view's BACKING
        storage (`view_source`): a movable owned local is moved or destroyed
        at the return, so a view of it dangles. Under `gen_yield` (the value
        of a generator's `yield`) a frame-resident local is stable
        frame-slot storage. Both flags hold only at a leaf the value reaches
        through no call; the closed-world reading keeps `gen_yield` through
        recorded sources, whose provenance it follows the whole way down."""
        calls = leaf.calls
        vs = view_source and not calls
        gy = gen_yield and (not calls or strict and all(
            c.how in (Lent.RECORDED, Lent.AWAITED) for c in calls))
        name = leaf.name
        if self._name_is_param_or_global(name):
            return True
        if not vs and self.ctx.func.bp_is_safe_to_return(name):
            return True
        return (gy and self._is_frame_resident_local(name)
                and self._local_has_borrowable_storage(leaf.node,
                                                       view_source=vs))

    def _leaves_dangle(self, ls: list[Leaf], *, view_source: bool = False,
                       gen_yield: bool = False, strict: bool = False) -> bool:
        """`is_dangling_return` as a mapping of the durability leaves: a
        temporary or an unclassified view node dangles; static storage, a
        call result no operand names and an unplaceable node dangle only in
        the closed-world reading; a name by the name rule
        (`_place_durable`)."""
        for leaf in ls:
            kind = leaf.kind
            if kind in (LeafKind.TEMPORARY, LeafKind.VIEWNODE):
                return True
            if kind is LeafKind.PLACE:
                if not self._place_durable(leaf, view_source=view_source,
                                           gen_yield=gen_yield,
                                           strict=strict):
                    return True
            elif kind is not LeafKind.NULL and strict:
                return True
        return False

    def _leaves_safe(self, ls: list[Leaf]) -> bool:
        """`is_safe_to_return_expr` as a mapping of the durability leaves:
        none dangles (open world), and none is an unplaceable node or an
        await the value reaches through no call -- nothing proves either
        outlives the frame."""
        if self._leaves_dangle(ls):
            return False
        for leaf in ls:
            for step in leaf.path:
                if step.kind is StepKind.CALL:
                    break
                if step.kind is StepKind.AWAIT:
                    return False
            else:
                if leaf.kind is LeafKind.UNPLACEABLE:
                    return False
        return True

    def _leaves_param_derived(self, ls: list[Leaf]) -> bool:
        """`is_param_derived_expr` as a mapping of the durability leaves:
        every leaf is static storage or a name by the name rule or bound
        `param_derived`, reached through recorded sources and borrowing
        constructors only."""
        for leaf in ls:
            if any(c.how not in (Lent.RECORDED, Lent.CONSTRUCTOR)
                   for c in leaf.calls):
                return False
            if leaf.kind is LeafKind.STATIC:
                continue
            if leaf.kind is not LeafKind.PLACE or not (
                    self._name_is_param_or_global(leaf.name)
                    or self.ctx.func.bp_is_param_derived(leaf.name)):
                return False
        return True

    def durability_leaves(self, expr: TpyExpr, *,
                          strict: bool = False) -> list[Leaf]:
        """Where the storage of a returned, yielded or bound `expr` lives
        (`leaves` under `ReturnLend`); `strict` is the closed-world
        reading."""
        return leaves(expr, ReturnLend(self.ctx, strict=strict), split=False)

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
        The roots are the names the borrow's storage lives in
        (`storage_roots` at HOLDER under `ReturnLend`) and every root a
        local among them is bound to.
        """
        if not self.ctx.rebound_globals:
            return None
        roots: set[str] = set()
        bt = self.ctx.func.borrow_tracker
        for name in storage_roots(expr, Use.HOLDER, None,
                                  ReturnLend(self.ctx)).names:
            roots.add(name)
            roots.update(src.root for src in bt.storage_roots_or_self(name))
        for name in sorted(roots):
            owner = self.ctx.rebound_globals.get(name)
            if owner is None or not self._name_is_module_global(name):
                continue
            leg = self._return_borrows_global_storage(name, return_type)
            if leg is not None:
                return name, owner, leg
        return None

    def is_param_derived_expr(self, expr: TpyExpr) -> bool:
        """Whether an expression's storage derives from parameters, `self`
        or globals (`_leaves_param_derived`)."""
        return self._leaves_param_derived(self.durability_leaves(expr))

    def is_safe_to_return_expr(self, expr: TpyExpr) -> bool:
        """Whether binding a local to this RHS makes the local safe to
        return (`_leaves_safe`). A superset of `is_param_derived_expr`,
        which the flow-merge intersection relies on."""
        return self._leaves_safe(self.durability_leaves(expr))

    def binding_durability(self, expr: TpyExpr) -> tuple[bool, bool]:
        """`is_param_derived_expr` and `is_safe_to_return_expr` of a
        binding's value, from one climb."""
        ls = self.durability_leaves(expr)
        return self._leaves_param_derived(ls), self._leaves_safe(ls)

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
        """Check if returning this expression would create a dangling reference
        (`_leaves_dangle` over `durability_leaves`).

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
        exemption fires only at a root the value reaches through no call (the
        closed-world reading follows recorded sources too).

        `assume_unknown_calls_safe`: the DIAGNOSTIC reading (the default)
        trusts a callee it cannot see through -- the callee is responsible for
        not handing back a dangling reference, and rejecting every opaque call
        would reject valid code. The generic yield-slot VERDICT reads the same
        climb closed-world instead (False): an unproven source must fall to the
        value slot rather than lend something the callee may have
        materialized -- an erased `Fn` result carries no ownership at all (see
        `docs/CALLABLE_CONTRACT_DESIGN.md`), and a callee whose body is not
        analyzed yet has no fact to read. Closed-world, a call is non-dangling
        only where its resolved signature borrows from operands that are
        themselves non-dangling.
        """
        strict = not assume_unknown_calls_safe
        return self._leaves_dangle(
            self.durability_leaves(expr, strict=strict),
            view_source=view_source, gen_yield=gen_yield, strict=strict)

    def borrow_temp_root(self, expr: TpyExpr) -> TpyExpr | None:
        """The fresh owned temporary a borrow-form `expr` PROVABLY points
        into, or None when no root is proven one: the first TEMPORARY leaf
        of the climb under `ReturnLend`'s proof reading. A callee whose
        borrow facts are unknown proves nothing, and neither does a name --
        whatever it is bound to lives outside the expression."""
        for leaf in leaves(expr, ReturnLend(self.ctx, proof=True),
                           split=False):
            if leaf.kind is LeafKind.TEMPORARY:
                return leaf.node
        return None

    def borrow_temp_roots(self, expr: TpyExpr, *,
                          stop: 'Callable[[TpyExpr], list[TempRoot] | None]'
                          ' | None' = None,
                          through: bool = False) -> 'list[TempRoot]':
        """Every fresh owned temporary a borrow-form `expr` MAY point into:
        the TEMPORARY leaves of the climb under `EveryTempLend`, and the
        operands of each node the climb cannot step past, climbed the same
        way (a subscript through a dunder its object and index, any other
        node its operands). A name proves nothing -- whatever it is bound
        to lives outside the expression. `stop` answers for a node the
        caller decides itself (None climbs on); `through` asks what `expr`'s
        value points into even when it is a new value itself (a new record
        may hold a view of an operand)."""
        if through and (isinstance(expr, TpyIfExpr) or (
                isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"))):
            # The climb starts an arm past its coercions, where `stop` must
            # still see the arm whole.
            arms = ((expr.then_expr, expr.else_expr)
                    if isinstance(expr, TpyIfExpr) else (expr.left, expr.right))
            return [r for arm in arms
                    for r in self.borrow_temp_roots(arm, stop=stop)]
        policy = EveryTempLend(self.ctx, expr, stop=stop, through=through)
        out: list[TempRoot] = []
        for leaf in leaves(expr, policy, split=False):
            if leaf.kind is LeafKind.TEMPORARY:
                out.append(TempRoot(leaf.node, in_tuple=leaf.temp_backed))
            elif leaf.kind is LeafKind.UNPLACEABLE:
                node = leaf.node
                parts = ([node.obj, *(node.index.children()
                                      if isinstance(node.index, TpySlice)
                                      else [node.index])]
                         if isinstance(node, TpySubscript)
                         else [e for _, e in every_lent_operand(node)])
                for part in parts:
                    out += self.borrow_temp_roots(part, stop=stop)
        return policy.found + out

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
            view_arg = borrowing_ctor_arg(inner)
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
        # A nullable borrow tuple lends exactly what its bare twin does, so
        # its non-None value takes the bare slot's element checks.
        opt_tuple = (escaping_borrow_tuple(return_type)
                     if isinstance(tuple_rt, OptionalType)
                     and not isinstance(peel_value_wrappers(expr),
                                        TpyNoneLiteral) else None)
        if opt_tuple is not None:
            tuple_rt = opt_tuple[0]
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

    def _tuple_literal_fresh_borrow_elems(
            self, tuple_type: TupleType, inner: TpyExpr) -> frozenset[int]:
        """The element indices whose non-value (borrow-form) slot has a
        fresh / dangling source in this tuple literal.

        Drives the owns-fresh-tuple-member assignment flag (see
        `update_tuple_member_local_facts`). The literal yield/return path has its
        own per-element loop (`_check_tuple_elem_dangle`), which is strictly
        stronger: it also catches a value coerced into a wrapper element via the
        pre-coercion source type, which this name-based flag cannot see.
        """
        if not isinstance(inner, TpyTupleLiteral):
            return frozenset()
        # Recursive-union wrappers are borrow form here too, so a fresh
        # wrapper member dangles like any other borrow element; the
        # is_dangling_return source check tells a fresh local from a
        # param/self-rooted wrapper, so there is no needs_wrapper() skip.
        return frozenset(
            i for i, et in enumerate(tuple_type.element_types)
            if i < len(inner.elements) and not et.is_value_type()
            and not isinstance(et, (OwnType, TypeParamRef))
            and self.is_dangling_return(inner.elements[i]))

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
        # A ternary returns whichever arm is taken -- check each.
        for inner in ternary_operands(expr):
            if isinstance(inner, TpyTupleLiteral):
                self._check_tuple_literal_elem_dangle(
                    tuple_type, inner, verb, source_type, gen_yield)

    def _check_tuple_literal_elem_dangle(
            self, tuple_type: TupleType, inner: TpyTupleLiteral, verb: str,
            source_type: TpyType | None, gen_yield: bool) -> None:
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
        fresh: frozenset[int] = frozenset()
        owning = False
        copy_into_own: list[int] = []
        borrow_roots: frozenset[SitedKey] = frozenset()
        if init_expr is not None and var_type is not None:
            tt = unwrap_readonly(var_type)
            if isinstance(tt, TupleType):
                fresh = self._derive_tuple_member_hazards(tt, init_expr)
                # A local bound from an owning call owns its elements whatever
                # its own type spells (per-element `Own` included).
                owning = self._derive_owning_storage(init_expr)
                if tt.has_pointer_repr_element():
                    borrow_roots = self._derive_tuple_borrow_sources(
                        name, tt, init_expr)
                copy_into_own = self._derive_copy_into_own_hazards(init_expr)
        # All four tuple-member hazard fields are (re)derived together and
        # replaced atomically; the rebind clears the name's old facts first
        # (so `t = t` re-installs its own, derived above). The per-element
        # reject/warn sets and the owning flag are flow-sensitive
        # (UNION-merged) so a hazard on any reaching path is caught at the
        # boundary check.
        self.ctx.func.bp_set_tuple_member(
            name,
            owns_fresh_idxs=fresh,
            owning_storage=owning,
            copy_into_own_idxs=frozenset(copy_into_own),
            borrow_source_roots=borrow_roots,
        )

    def _derive_tuple_borrow_sources(self, name: str, tt: 'TupleType',
                                     init_expr: TpyExpr
                                     ) -> frozenset[SitedKey]:
        """The storage roots a borrow-form tuple local's element pointers
        alias, so the mark functions can trace a later yield/return of the
        bare name back to the aliased params. Roots are expanded through
        already-recorded tuple locals at record time (values stay terminal,
        so mark-time lookup is single-level and cycle-free); a
        self-assignment keeps the old sources via the derive-before-clear
        order above. Stored on BindingProvenance (UNION-merged at branch
        joins), so branch-divergent binds accumulate both arms' sources.
        Each root keeps the binding its name names HERE.
        """
        sources: set[SitedKey] = set()
        for root in slot_lent_roots(init_expr, tt, Use.HOLDER, None,
                                    MayLend(self.ctx)).proven().names:
            expanded = self.ctx.func.bp_borrow_source_roots(root)
            for s in (expanded if expanded else (SitedKey(
                    StorageKey.of_name(root), self.ctx.binding_site(root)),)):
                if s.key.root != name:
                    sources.add(s)
        return frozenset(sources)

    def _is_owning_tuple_call(self, expr: TpyExpr) -> bool:
        """Whether `expr` is a call returning OWNING tuple storage --
        `Own[tuple[...]]` or a tuple with per-element `Own` slots
        (`tuple_source_elem_owned` owns an element). A local bound from one
        owns its element storage (the call's return ABI is the storage
        form), unlike a borrow-form tuple call result whose pointers the
        callee already proved durable.
        """
        # A ternary is no call: whether its arms own is each arm's fact.
        operands = ternary_operands(expr)
        inner = operands[0]
        if len(operands) > 1 or not isinstance(inner, (TpyCall,
                                                       TpyMethodCall)):
            return False
        fi = inner.resolved_function_info
        if fi is None:
            return False
        return any(tuple_source_elem_owned(self.ctx, inner, fi.return_type)
                   or ())

    def _check_own_element_slots(self, actual: TupleType, expected: TupleType,
                                 source_expr: TpyExpr | None) -> frozenset[int]:
        """The `Own` element paths of a tuple slot (`tuple[Own[A], int32]`,
        a nested `tuple[tuple[Own[A], int32], int32]` included) fed by a
        whole tuple source: the one owned-tuple sink check
        (`check_owned_tuple_sink`). Returns the top-level elements it
        decided, which the element-wise recursion then checks without the
        source. A NAME's elements -- a walrus binding's included -- are its
        own check's (`check_name_borrow_into_own`), a literal's its
        members'."""
        none: frozenset[int] = frozenset()
        if source_expr is None or self.tuple_name_source(source_expr):
            return none
        if isinstance(peel_coerce(source_expr), TpyTupleLiteral):
            return top_level(owned_slot_paths(expected))
        # Only the source's own tuple: a nested element's recursion arrives
        # with the outer source, whose paths were decided here already.
        st = self.ctx.get_expr_type(source_expr)
        st = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
              if st is not None else None)
        if not (isinstance(st, TupleType) and st == TupleType(tuple(
                unwrap_ref_type(et) for et in actual.element_types))):
            return none
        owned = owned_slot_paths(expected)
        if not owned:
            return none
        if self.ctx.slot_step_commits():
            self.check_owned_tuple_sink(source_expr, expected,
                                        "owned storage", source_expr)
        if self.per_leaf_source(source_expr):
            return top_level(owned)
        # A name read behind a wrapper keeps its HEDGED leaves for the
        # element-wise recursion, whose whole-tuple hedge declares that copy.
        open_leaf = top_level(
            p for p in owned if self.copy_permission(_element_at(expected, p))
            is LeafPermission.HEDGED)
        return top_level(owned) - open_leaf

    @staticmethod
    def tuple_name_source(expr: TpyExpr | None) -> bool:
        """Whether a tuple source is a NAME read, whose copies follow the
        name's binding facts rather than its leaves. A walrus is its target
        name's read: the literal it binds is captured into the name, which
        stays live."""
        return isinstance(peel_coerce(expr) if expr is not None else None,
                          (TpyName, TpyNamedExpr))

    def per_leaf_source(self, expr: TpyExpr) -> bool:
        """Whether a tuple source takes the per-leaf copy rule at an owning
        sink by the leaves it lends: every one -- a union leaf and an open
        `T` leaf included -- is warned (or hedged) on its own. Not a NAME
        (`tuple_name_source`), whose binding facts say which leaves it
        moves in, nor a name read behind a wrapper, whose HEDGED leaves
        take the element-wise recursion's whole-tuple hedge."""
        if self.tuple_name_source(expr):
            return False
        operands = ternary_operands(expr)
        return not (len(operands) == 1
                    and self.tuple_name_source(operands[0]))

    def check_owned_tuple_sink(self, source_expr: TpyExpr, slot: TpyType,
                               dest: str, loc_node: TpyExpr | TpyStmt, *,
                               moved: frozenset[ElemPath] = frozenset(),
                               elem_path: str = "",
                               sink: 'TupleSink | None' = None,
                               pname: str = "",
                               nocopy_hint: str = NOCOPY_REMEDIATION_HINT
                               ) -> frozenset[int]:
        """The one check every owned tuple sink runs, and its one
        registration for the per-leaf record (`register_sink`): each
        element path the slot owns (`owned_slot_paths`) that the source
        lends (`tuple_lent_paths`) and does not move in (`moved`) is
        deep-copied where CPython aliases, warned per path into `dest` (an
        error for a non-copyable one, the hedge for an open `T`). A LITERAL
        is checked per member at the `sink` its caller names
        (`check_tuple_literal_members`, `pname` naming an ARG sink's
        parameter); without one its members were the caller's. An explicit
        `copy()` copies nothing unasked, and a NAME goes to
        `warn_name_tuple_copy`, a non-copyable leaf's error naming
        `nocopy_hint` as its remedy. `sink` None is an `Own` slot (a return
        or an argument). `elem_path` prefixes the member path of a tuple
        reached inside another one, which the outer source's registration
        covers. Returns the top-level indices it copies."""
        peeled = peel_value_wrappers(source_expr)
        literal = (isinstance(peeled, TpyTupleLiteral)
                   and not self.tuple_name_source(source_expr))
        if not elem_path and (sink is not None or not literal):
            self.register_sink(source_expr, slot, sink, per_leaf=True)
        none: frozenset[int] = frozenset()
        tt = bare_tuple(slot)
        if tt is None:
            return none
        if self.tuple_name_source(source_expr):
            return self.warn_name_tuple_copy(slot, dest, loc_node, elem_path,
                                             moved=moved, source=source_expr,
                                             nocopy_hint=nocopy_hint)
        if isinstance(peeled, TpyTupleLiteral):
            if sink is None:
                return none
            members = own_tuple_target(unwrap_readonly(unwrap_ref_type(slot)))
            return self.check_tuple_literal_members(
                peeled, members if members is not None else tt, sink, dest,
                elem_path, pname=pname,
                whole=isinstance(unwrap_ref_type(slot), OwnType))
        if self.is_copy_call(source_expr):
            return none
        per_leaf = self.per_leaf_source(source_expr)
        lent = self.tuple_lent_paths(source_expr, tt) or none
        copied = top_level(self._warn_tuple_path_copies(
            tt, (lent & owned_slot_paths(slot)) - moved, dest, loc_node,
            elem_path, source=source_expr, hedge_open=per_leaf))
        return copied

    def tuple_lent_paths(self, expr: TpyExpr | None, slot: TpyType | None,
                         prefix: ElemPath = ()) -> frozenset[ElemPath] | None:
        """The element paths a tuple source lends as it ARRIVES at an owning
        sink (`tuple_borrowed_paths`' incoming question): a literal member
        lends what it does itself (`member_lends`), however its literal
        captures it, and a call -- or an element read off one -- what its
        result does (`call_lent_paths`)."""
        return tuple_borrowed_paths(self.ctx, expr, slot, prefix,
                                    incoming=self)

    def call_lent_paths(self, call: TpyCall | TpyMethodCall,
                        slot: TupleType,
                        prefix: ElemPath = ()) -> frozenset[ElemPath]:
        """The paths of tuple `slot` at `prefix` a CALL's result lends as
        it arrives at an owning sink: the leaves the sink copies. A call with
        a lend contract (`borrows=` / `element_of=`, which
        `stamp_result_borrow` stamps) lends its declared return's paths
        (`declared_call_paths`) exactly when the scalar twin's result would
        be a borrowed source (`returns_borrow`: a copy of an operand the
        program still reaches), and nothing otherwise; a call without one
        lends per its declared return. An open `T` leaf among them takes
        the sink's per-leaf hedge."""
        if (call.result_form.is_declared
                and not returns_borrow(self.ctx, call)):
            return frozenset()
        return declared_call_paths(call, slot, prefix)

    def warn_argument_tuple_copies(self, call: TpyCallLike, slot: TupleType,
                                   dest: str) -> None:
        """A tuple argument that is a stamped call's COPY result
        (`CallAnalyzer.defer_argument_copies`): each leaf the call lends
        (`tuple_lent_paths`) at an element whose slot is a mutable borrow
        surface takes its scalar twin's argument check. Only an open `T`
        leaf reaches here -- `stamp_result_borrow` refuses a tuple holding a
        class instance -- so each is the hedge, at declaration time as the
        scalar's is."""
        paths = [p for p in self.tuple_lent_paths(call, slot) or ()
                 if param_has_mutable_borrow_surface(_element_at(slot, p))]
        # A concrete reference leaf would need the scalar's WARNED check,
        # not the hedge: this holds only while that refusal stands.
        assert all(open_tuple_leaf(_element_at(slot, p)) for p in paths), (
            slot, paths)
        self._warn_tuple_path_copies(slot, paths, dest, call, source=call,
                                     hedge_open=True)

    def member_lends(self, m: TpyExpr) -> bool:
        """Whether one scalar member of a tuple literal arrives borrowed:
        storage that outlives it (`arrives_borrowed`); never an explicit
        `copy()`."""
        if self.is_copy_call(m):
            return False
        return self.arrives_borrowed(m)

    def _warn_tuple_path_copies(self, tt: TupleType,
                                paths: Iterable[ElemPath], dest: str,
                                loc_node: TpyExpr | TpyStmt,
                                elem_path: str = "", *,
                                source: TpyExpr,
                                hedge_open: bool = False,
                                nocopy_hint: str = NOCOPY_REMEDIATION_HINT,
                                note_read: 'TpyExpr | None' = None,
                                note_errors: bool = False,
                                ) -> frozenset[ElemPath]:
        """Warn the deep copy of each reference leaf of `tt` at a path in
        `paths` into `dest`, by the record's rule (`copy_permission`): an
        error for a non-copyable one, the owning slot's hedge
        (`defer_own_copy_verdict`) for a HEDGED one where `hedge_open` --
        otherwise the caller's whole-tuple hedge -- and the copy warning for
        a WARNED one. Each one declares the copy of that path of `source`,
        the tuple `tt` types; the note on the binding of name read
        `note_read` at the path follows each warning's remedy, and each
        error's `nocopy_hint` where `note_errors`. Returns the paths it
        warned or hedged."""
        out: set[ElemPath] = set()
        for path in sorted(paths):
            et = _element_at(tt, path)
            leaf = unwrap_readonly(unwrap_ref_type(et))
            # A value leaf's copy, or one a `ValueType` bound settles
            # (`T`, `T | int32`), is unobservable.
            if (leaf.is_value_type()
                    or self.ctx.copy_is_unobservable(peel_slot(et))):
                continue
            shown = elem_path + ".".join(str(i) for i in path)
            where = f"{dest} (tuple element {shown})"
            note = (self.binding_note(note_read, path=path)
                    if note_read is not None else None)
            # A union copies the alternative it holds, so one non-copyable
            # alternative leaves it no copy to make, whatever an open
            # alternative turns out to be (BUGS.md#union-nocopy-field-store).
            if self.ctx.is_type_non_copyable(et) or (
                    isinstance(leaf, UnionType)
                    and any(self.ctx.is_type_non_copyable(m)
                            for m in leaf.members)):
                self.ctx.copy_error(
                    lambda n, et=et, where=where: f"cannot copy non-copyable "
                    f"type '{et}' into {where}{nocopy_hint}"
                    f"{n if note_errors else ''}", loc_node, source, path,
                    note=note)
                out.add(path)
                continue
            if self.copy_permission(et) is LeafPermission.HEDGED:
                if hedge_open:
                    # The hedge names the element as declared, its `Send` /
                    # `Sync` marker kept, as a literal member's does.
                    shown_t = unwrap_readonly(unwrap_ref_type(
                        _element_at(tt, path, keep_markers=True)))
                    self.ctx.defer_own_copy_verdict(
                        shown_t, shown_t, where, loc_node, source=source,
                        path=path)
                    out.add(path)
                continue
            shown = self.diag_type(et)
            self.ctx.copy_warning(
                lambda n, shown=shown, where=where: f"copies {shown} into "
                f"{where}; use copy() to make this explicit{n}", loc_node,
                source, path, note=note)
            out.add(path)
        return frozenset(out)

    def check_tuple_literal_members(self, literal: TpyTupleLiteral,
                                    tuple_type: TpyType, sink: TupleSink,
                                    dest: str, elem_path: str = "", *,
                                    pname: str = "",
                                    whole: bool = False) -> frozenset[int]:
        """Per-member copy check for a tuple LITERAL at `sink`: member i takes
        the rule a scalar of its type takes at that slot (`tuple-equals-
        scalar`, `_check_literal_member`). `pname` names the parameter at an
        ARG sink; `whole` says the slot is an `Own[tuple[...]]`, which owns
        every leaf of a nested member too. Returns the elements whose
        member copies (warned, or an error for a non-copyable one)."""
        if not isinstance(tuple_type, TupleType):
            return frozenset()
        fired: set[int] = set()
        for i, et in enumerate(tuple_type.element_types):
            if i < len(literal.elements) and self._check_literal_member(
                    literal.elements[i], et, sink, dest, f"{elem_path}{i}",
                    pname, whole=whole):
                fired.add(i)
        return frozenset(fired)

    def _check_literal_member(self, m: TpyExpr, et: TpyType, sink: TupleSink,
                              dest: str, path: str, pname: str, *,
                              whole: bool) -> bool:
        """The one dispatch of literal member `m` at element type `et` of a
        literal at `sink`. A NAME read -- a scalar's or a tuple's -- takes
        the rule the same read takes alone at the member's owning slot
        (`check_member_read`): an `Own`-marked member at a return or an
        argument that slot's `Own` rule, a member of owned storage (a yield's
        `Own`-marked one, an unmarked reference at a field or a container)
        the storage rule. A nested literal's members are checked at
        `nested_member_sink`; an `Own`-marked tuple member's other sources
        lend per leaf. Returns whether the member copies."""
        owned = unwrap_optional_own(et) is not None
        own: 'tuple[str, str] | None' = None
        if own_slot_member(sink, owned):
            own = ((f"tuple element {path}", "return")
                   if sink is TupleSink.RETURN
                   else (f"argument '{pname}' tuple element {path}", "pass"))
        nested = bare_tuple(et)
        if nested is not None and self.tuple_name_source(m):
            # The literal's render copies a tuple member even at its last
            # use (BUGS.md#tuple-literal-name-member-silent-copy).
            slot = (OwnType(nested)
                    if member_owns_every_leaf(sink, owned, whole) else et)
            return self.check_member_read(m, slot, dest, path,
                                          last_use_moves=False)
        if nested is None:
            if own is not None:
                own_et = unwrap_optional_own(et)
                assert own_et is not None
                return self.check_own_lvalue_into_own(
                    own_et, m, own[0], action=own[1], whole_slot=False)
            return (literal_member_storage_copy(sink, owned, et, m)
                    and self.check_member_read(
                        m, unwrap_own(et), dest, path,
                        last_use_moves=sink in _LAST_USE_MOVES_SINKS))
        if own is None:
            return self._check_nested_tuple_member(
                m, nested, nested_member_sink(sink, owned, whole), dest,
                f"{path}.", pname,
                whole=member_owns_every_leaf(sink, owned, whole))
        nested_own = self._own_tuple_member(m, et)
        if isinstance(nested_own, TpyTupleLiteral):
            # An `Own[tuple[...]]` member owns each leaf of its literal's
            # members.
            return bool(self.check_tuple_literal_members(
                nested_own, own_tuple_target(et), sink, dest, f"{path}.",
                pname=pname, whole=True))
        if nested_own is not None:
            # Any other source of one lends per leaf, as a whole source at
            # that slot does.
            return bool(self.check_owned_tuple_sink(
                m, et, dest, m, elem_path=f"{path}."))
        # A name read behind a wrapper (`per_leaf_source`): the slot's
        # rule whole.
        return self.check_own_lvalue_into_own(et, m, own[0], action=own[1],
                                              whole_slot=False)

    def _own_tuple_member(self, m: TpyExpr,
                          et: TpyType) -> 'TpyExpr | None':
        """The source a literal member `m` at `Own`-marked element `et`
        hands to an `Own[tuple[...]]` element checked per leaf: the nested
        literal (checked per member), or a source that takes the per-leaf
        rule (`per_leaf_source`); None for a scalar element, and for a name
        (its binding facts')."""
        if bare_tuple(et) is None:
            return None
        inner = peel_value_wrappers(m)
        if (isinstance(inner, TpyTupleLiteral)
                and not self.tuple_name_source(m)):
            return inner
        return m if self.per_leaf_source(m) else None

    def _check_nested_tuple_member(self, m: TpyExpr, nested: TupleType,
                                   sink: 'TupleSink | None', dest: str,
                                   path: str, pname: str, *,
                                   whole: bool = False) -> bool:
        """A nested value-tuple member `m` other than a name, its members
        checked at `sink` (the outer literal's `nested_member_sink`). At a
        CONTAINER the nested tuple is owned storage
        (`warn_storage_tuple_copy`). At a return, a yield and an argument a
        literal's members take the declared slot's `Own` markers -- every
        leaf under an `Own[tuple[...]]` slot (`whole`) -- and another
        source the owned tuple sink's decision."""
        if sink is TupleSink.CONTAINER:
            return self.warn_storage_tuple_copy(m, nested, dest, path)
        peeled = peel_value_wrappers(m)
        if isinstance(peeled, TpyTupleLiteral):
            members = own_tuple_target(OwnType(nested)) if whole else None
            return bool(self.check_tuple_literal_members(
                peeled, members if members is not None else nested, sink,
                dest, path, pname=pname, whole=whole))
        if not self.per_leaf_source(m):
            return False
        slot: TpyType = OwnType(nested) if whole else nested
        return bool(self.check_owned_tuple_sink(
            m, slot, dest, m, elem_path=path))

    def check_member_read(self, m: TpyExpr, slot: TpyType, dest: str,
                          path: str, *, last_use_moves: bool,
                          sink: 'TupleSink | None' = None) -> bool:
        """A read `m` landing in the owning `slot` of the tuple literal
        member at `path` ("" for a tuple name filling a whole container
        element, registered at `sink`): the rule the same read takes
        standing alone at that slot, one move decision for both shapes. A
        name's last use moves in what it owns, unless the sink's render
        copies it all the same (not `last_use_moves`) and a leaf that copy
        takes has no copy to make; a tuple read still copies the leaves it
        does not own (`name_copied_paths`). The copy is then the shape's:
        a sink's in its words (`register_sink_copy`), a tuple name's per
        leaf the slot owns (`check_owned_tuple_sink`). An `Own` slot's
        member is `check_own_lvalue_into_own`'s. Returns whether anything
        is copied."""
        tt = bare_tuple(slot)
        name = m if isinstance(m, TpyName) else None
        copy: 'SinkCopy | None' = None
        elem_path = f"{path}." if path else ""
        if tt is not None:
            owned = owned_slot_paths(slot)
            if not owned:
                return False
            granted = name is not None and self.claim_name_move(name)
        else:
            where = f"{dest} (tuple element {path})"
            if self.is_copy_call(m):
                return False
            if not (self.arrives_borrowed(m)
                    or self._ternary_member_copies(m)):
                self.check_unobserved_call_copy(m, slot, where)
                return False
            copy = SinkCopy(slot, slot, where)
            granted = self.claim_name_move(
                m, OwnType(slot), f"tuple element {path}", "pass",
                whole_slot=False, sink=copy)
        render_copy_fails = (not last_use_moves
                             and self._render_copy_fails(slot, tt))
        moves = granted and not render_copy_fails
        # A last use whose render copies all the same: no auto-move to offer.
        hint = NOCOPY_NO_AUTO_MOVE_HINT if granted and not moves else None
        if copy is not None:
            if moves:
                return False
            if granted:
                # The read registered its copy; the render copies it all
                # the same.
                return True
            self.register_sink_copy(copy, m, nocopy_hint=hint)
            return True
        assert tt is not None
        moved: frozenset[ElemPath] = frozenset()
        if moves:
            assert name is not None
            moved = reference_paths(tt) - self.name_copied_paths(
                name.name, tt, slot)
        return bool(self.check_owned_tuple_sink(
            m, slot, dest, m, moved=moved, elem_path=elem_path, sink=sink,
            nocopy_hint=NOCOPY_REMEDIATION_HINT if hint is None else hint))

    def _render_copy_fails(self, slot: TpyType,
                           tt: 'TupleType | None') -> bool:
        """Whether a render that copies a read into owning `slot` (`tt` its
        tuple) has a non-copyable leaf to copy: the scalar payload, or a
        reference leaf of the tuple the slot owns (`owned_slot_paths`) -- a
        leaf the slot borrows is not copied."""
        if tt is None:
            return self.ctx.is_type_non_copyable(unwrap_own(slot))
        return any(self.ctx.is_type_non_copyable(_element_at(tt, p))
                   for p in owned_slot_paths(slot))

    def name_copied_paths(self, name: str, tt: TupleType,
                          slot: TpyType) -> frozenset[ElemPath]:
        """The leaf paths of tuple `tt` the owning `slot` holds
        (`owned_slot_paths`) that a read of tuple name `name` handing over
        what it owns still copies: those a binding of it borrows
        (`nonowning_leaves`), those under an element its current binding
        holds by reference, and those a parameter's, loop variable's or
        global's declared type lends (`declared_lent_paths`). The one
        answer for a tuple name standing alone at a return or an argument
        (`check_name_borrow_into_own`) and for one inside a tuple literal
        (`check_member_read`)."""
        fs = self.ctx.func
        refs = reference_paths(tt)
        held = fs.bp_copy_into_own_idxs(name)
        return owned_slot_paths(slot) & (
            fs.nonowning_leaves(name, refs)
            | frozenset(p for p in refs if p[0] in held)
            | self.declared_lent_paths(name, len(tt.element_types)))

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

    def copy_remedy(self, expr: 'TpyExpr | None',
                    note: 'str | None' = None) -> str:
        """The remedy clause of an owning slot's copy warning for `expr`,
        ending with `note` (the binding's, `ownership_note`, when None)."""
        clause = (CONSUMING_FIELD_COPY_CLAUSE
                  if self.is_consuming_field_borrow(expr) else "")
        if note is None:
            note = self.ownership_note(expr)
        return f"use copy() to make this explicit{clause}{note}"

    def nocopy_hint(self, expr: 'TpyExpr | None',
                    index: 'int | None' = None, *,
                    note: 'str | None' = None) -> str:
        """The parenthesized remedy of a non-copyable copy error for `expr`
        (for its tuple element `index`), ending with `note` (the binding's,
        `ownership_note`, when None) unless it is a consuming method's
        field's."""
        if self.is_consuming_field_borrow(expr):
            return f" ({CONSUMING_FIELD_MOVE_NOTE})"
        inner = expr.expr if isinstance(expr, TpyCoerce) else expr
        if note is None:
            note = self.ownership_note(expr, index)
        if inner is not None and inner in self.ctx.repeated_reads:
            return NOCOPY_NO_AUTO_MOVE_HINT + note
        return NOCOPY_REMEDIATION_HINT + note

    def ownership_note(self, expr: 'TpyExpr | None',
                       index: 'int | None' = None, *,
                       path: 'ElemPath | None' = None) -> str:
        """Why an owning sink copies a read of a non-owning local (or of an
        element some binding of the tuple local borrows -- `expr[i]`, or
        the tuple name with its element `index` or leaf `path`): the
        binding that made it refer to existing storage. Empty for any other
        source."""
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
        if isinstance(expr, TpySubscript) and index is None:
            index = const_tuple_index(expr.index)
            expr = expr.obj
            while isinstance(expr, TpyCoerce):
                expr = expr.expr
            if index is None:
                return ""
        if not isinstance(expr, TpyName) or expr in self.ctx.comp_var_reads:
            return ""
        fs = self.ctx.func
        site_line = self._site_line(expr, path if path is not None
                                    else (index,) if index is not None
                                    else None)
        if site_line is not None:
            return (f" ('{expr.name}' is bound to existing storage at line "
                    f"{site_line})" if site_line else "")
        line = fs.nonowning_line(
            expr.name, path if path is not None
            else (index,) if index is not None and index >= 0 else None)
        if line is None:
            return ""
        if index is not None and path is None:
            arity = self._tuple_arity(expr)
            if arity is None:
                return ""
            if index < 0:
                index += arity
            if index not in fs.nonowning_elems(expr.name, arity):
                return ""
        # A temporary of a desugar is shown as the variable it holds a
        # value for.
        func = fs.current_function
        shown = ((program_names(expr.name, func.body)[0] or expr.name)
                 if self.ctx.in_function_body() else expr.name)
        return f" ('{shown}' is bound to existing storage at line {line})"

    def _site_line(self, read: TpyName,
                   at: 'ElemPath | None') -> int | None:
        """The line of the binding inside the block of the binding SITE
        name read `read` resolved to that made it refer to existing
        storage (at element path `at`, when given): 0 for a site whose
        bindings borrow nothing there, None for no settled site entry with
        a line (the function-wide fact answers)."""
        binding = self.ctx.read_bindings.get(read)
        if not (binding is not None and binding.kind is ReadKind.SITE
                and binding.node in self.ctx.site_ownership):
            return None
        own = site_ownership(self.ctx, binding.node, binding.name)
        if own.line is None:
            return None
        covers = (any(at[:len(b)] == b for b in own.borrowed)
                  if at is not None
                  else any(len(b) <= 1 for b in own.borrowed))
        return own.line if covers else 0

    def _nonowning_line(self, read: TpyName) -> int | None:
        """The line of the binding that made what name read `read` holds
        refer to existing storage: its site's, else the local's."""
        site = self._site_line(read, None)
        if site is not None:
            return site or None
        return self.ctx.func.nonowning_line(read.name)

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
        a borrow-returning call, a tuple lending an element
        (`tuple_lent_paths`), a walrus (whatever its value: it names the binding it
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
        # A tuple literal is fresh: what its members lend is their own
        # check's.
        lent = (self.tuple_lent_paths(expr, self.ctx.get_expr_type(expr))
                if not isinstance(peel_value_wrappers(expr), TpyTupleLiteral)
                else None)
        if lent is not None:
            return bool(lent)
        if self.is_lvalue(expr) or returns_borrow(self.ctx, expr):
            return True
        inner = expr.expr if isinstance(expr, TpyCoerce) else expr
        if isinstance(inner, TpyNamedExpr):
            return True
        # A member of a call's result is the temporary's own only when the
        # result is PROVEN fresh and not a borrow: a `Callable` value's
        # by-value result may be a copy of a borrow its target returned,
        # which `returns_borrow` calls fresh
        # (BUGS.md#callable-value-borrow-return-copies-unwarned). Through a
        # pointer it is the pointee's, whatever handed the pointer back.
        # Off any other object it is borrowed where the object is, or where
        # C++ reads the object as an lvalue (an element of a temporary
        # container), which copies the member.
        if isinstance(inner, TpyFieldAccess) and inner.hidden_call is None:
            obj = peel_coerce(inner.obj)
            if isinstance(unwrap_qualifiers(self.ctx.get_expr_type(obj)),
                          PtrType):
                return True
            if isinstance(obj, TpyCallLike):
                return (returns_borrow(self.ctx, obj)
                        or not call_result_is_fresh(self.ctx, obj,
                                                    proof=True))
            return (self.arrives_borrowed(obj)
                    or not is_rvalue_source(self.ctx, obj))
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
            # The read's own type: the record's build asks this after the
            # scope that bound the name is gone.
            read_type = self.ctx.get_expr_type(arm)
            if read_type is None:
                continue
            if not unwrap_qualifiers(read_type).is_value_type():
                return True
        return False

    def warn_storage_tuple_copy(self, elem: TpyExpr, tuple_type: TpyType,
                                dest: str, elem_path: str = "") -> bool:
        """Value-tuple-with-reference-member copy diagnostic for an
        owned-storage element. A literal's members are gated per member
        expression, every other source by the one owned-tuple sink check.
        Returns whether anything fired."""
        peeled = peel_coerce(elem)
        if isinstance(peeled, TpyTupleLiteral):
            if elem_path:
                return bool(self.check_tuple_literal_members(
                    peeled, tuple_type, TupleSink.CONTAINER, dest, elem_path))
            return bool(self.check_owned_tuple_sink(
                elem, tuple_type, dest, elem, sink=TupleSink.CONTAINER))
        if self.tuple_name_source(elem):
            # A container element owns every leaf. A whole element's last
            # use moves in; a literal member's renders a copy.
            return self.check_member_read(
                elem, OwnType(tuple_type), dest, elem_path[:-1],
                last_use_moves=not elem_path, sink=TupleSink.CONTAINER)
        return bool(self.check_owned_tuple_sink(
            elem, OwnType(tuple_type), dest, elem,
            elem_path=elem_path, sink=TupleSink.CONTAINER))

    def warn_name_tuple_copy(self, slot: TpyType, dest: str,
                             loc_node: TpyExpr | TpyStmt,
                             elem_path: str = "", *,
                             moved: frozenset[ElemPath] = frozenset(),
                             source: TpyExpr,
                             nocopy_hint: str = NOCOPY_REMEDIATION_HINT
                             ) -> frozenset[int]:
        """The copy of tuple NAME read `source` into an owning `slot`: a
        name lends every reference path, so each one the slot owns
        (`owned_slot_paths`) -- but those the read moves in (`moved`) -- is
        warned or hedged per path (an error for a non-copyable one, naming
        `nocopy_hint`). Returns the top-level indices copied."""
        tt = bare_tuple(slot)
        if tt is None:
            return frozenset()
        # Every leaf the slot copies registers its copy; the record says
        # which the read moves.
        return top_level(self._warn_tuple_path_copies(
            tt, owned_slot_paths(slot), dest, loc_node, elem_path,
            source=source, hedge_open=True, nocopy_hint=nocopy_hint,
            note_read=source) - moved)

    def register_sink(self, source: TpyExpr | None,
                      slot: TpyType | None, sink: 'TupleSink | None',
                      *, per_leaf: bool = False,
                      binds: 'TpyStmt | TpyExpr | None' = None,
                      declares_copies: bool = True) -> None:
        """An owning sink's one registration of the source it takes and its
        slot (`sink` None: an `Own` slot), for the per-leaf record built
        once the body's ownership fact is settled (`build_source_records`),
        where the slot holds a reference leaf (`holds_reference_leaf`). The
        source is kept as written: a walrus is its target name's read. A
        NAME read's binding is the one its read resolved to
        (`read_bindings`).

        A tuple -- a tuple slot, or a tuple source at an element slot its
        coercion recurses into -- registers only from its per-leaf check
        (`per_leaf`), which knows the sink each member lands at; a store
        into `Any`, which no per-leaf check takes, registers its tuple
        payload itself.

        A source registers once. The statement's own check (a `sink` kind)
        stands over the `Own`-slot check its coercion ran on the same
        source, before or after it; otherwise the first registration -- the
        outermost slot, before the element slots it recurses into --
        stands."""
        if (source is None or slot is None
                or not self.records_slot(slot, source)):
            return
        if (not per_leaf and sink is not TupleSink.ANY
                and (bare_tuple(slot) is not None
                     or bare_tuple(self.ctx.get_expr_type(source))
                     is not None)):
            return
        if (sink in (TupleSink.FIELD, TupleSink.CONTAINER, TupleSink.ANY)
                and not isinstance(unwrap_ref_type(slot), OwnType)):
            # Owned storage owns every leaf it holds.
            slot = OwnType(unwrap_own(unwrap_ref_type(slot)))
        sinks = self.ctx.func.tuple_sinks
        key = peel_value_wrappers(source)
        if self.ctx.func.settled and key in self.ctx.source_records:
            return
        if not self.ctx.func.settled:
            self.demote_element_read(key)
        prev = sinks.get(key)
        # A walrus's value is the binding's source and, through the walrus,
        # its enclosing sink's: the enclosing sink's registration stands.
        if prev is not None and binds is not None:
            return
        if (prev is not None and prev.binds is None
                and (prev.sink is not None or sink is None)):
            return
        self.ctx.assert_slot_effect("an owning sink's registration")
        sinks[key] = SinkRead(source, slot, sink, binds,
                              declares_copies)
        if self.ctx.func.settled:
            # A sink checked once the body settled reads its verdicts now.
            self.build_source_records()

    def register_owned_result(self, source: TpyExpr | None, slot: TpyType,
                              sink: 'TupleSink') -> None:
        """The registration of an owning slot's source that may already
        have the slot's type -- a `copy()`, an `Own` result -- and so passes
        no coercion that would register it: at a slot owning a leaf
        (`_slot_owned_paths`), an Optional's `Own` or tuple payload's
        included. A tuple payload registers per leaf where no per-leaf check
        takes the source (a literal's, a name's or a walrus's does). Any
        other Optional is stored whole (`std::optional<T>`) and registers
        as itself. The one rule of the return, the argument, the lambda
        result and the yield."""
        bare = unwrap_readonly(slot)
        if isinstance(bare, OptionalType):
            inner = unwrap_readonly(bare.inner)
            if isinstance(inner, OwnType) or bare_tuple(inner) is not None:
                bare = inner
        whole = unwrap_readonly(slot)
        if (sink is TupleSink.YIELD and source is not None
                and isinstance(whole, (OptionalType, UnionType))
                and not isinstance(bare, OwnType)
                and not whole.is_value_type()):
            # A frame stores what it yields: owned storage, whose copies
            # no check declares yet
            # (BUGS.md#yield-record-into-union-slot-refused).
            self.register_sink(source, OwnType(unwrap_readonly(slot)), sink,
                               declares_copies=False)
            return
        if source is None or not self._slot_owned_paths(
                SinkRead(source, bare, sink)):
            return
        per_leaf = (bare_tuple(bare) is not None and not isinstance(
            peel_coerce(source), (TpyTupleLiteral, TpyName, TpyNamedExpr)))
        self.register_sink(source, bare, sink, per_leaf=per_leaf)

    def check_tuple_result(self, value: TpyExpr, slot: TpyType,
                           sink: 'TupleSink', *, pname: str = "") -> None:
        """The copy check of a tuple `value` at the declared tuple `slot`
        of an owning sink -- `Own[tuple[...]]` included, which owns every
        leaf, and `X | None`, whose non-None value it is -- one dispatch
        per source: a literal's members take their scalar's rule at `sink`
        (`pname` naming an ARG sink's parameter), a NAME its binding facts
        (`check_name_borrow_into_own`), a closure's capture read in place.
        A walrus is its target name's read, whose copies the coercion
        declares as a live name's. Any other source's copies are the
        coercion's."""
        slot = unwrap_readonly(slot)
        if isinstance(slot, OptionalType):
            slot = unwrap_readonly(slot.inner)
        target = own_tuple_target(slot)
        if target is None:
            return
        read = peel_coerce(value)
        if isinstance(read, TpyTupleLiteral):
            self.check_owned_tuple_sink(read, slot, "owned storage", read,
                                        sink=sink, pname=pname)
        elif isinstance(read, TpyNamedExpr):
            self.register_sink(read, slot, sink, per_leaf=True)
        elif isinstance(read, TpyName):
            self.check_name_borrow_into_own(read.name, target, read, slot,
                                            sink)

    def reads_capture(self, name: str) -> bool:
        """Whether `name` read in this body is a nested def's capture of
        its enclosing scope's binding (a nested def cannot bind an
        enclosing name of its own without `nonlocal`). Read off the
        enclosing scope's names: the def's `captured_names` are set only
        once its whole body is analyzed."""
        fs = self.ctx.func
        return (fs.in_nested_def and name in fs.outer_scope_locals
                and name not in fs.current_param_names)

    def records_slot(self, slot: TpyType, source: TpyExpr) -> bool:
        """Whether an owning slot's source gets a record: one holding a
        reference leaf (`holds_reference_leaf`), or an owned buffer passed
        as a view elsewhere (`Own[str]`, `Own[bytes]`:
        `has_view_param_form`) a local moves into at its last use rather
        than copying the buffer.
        A parameter handed on keeps its copy: MIR does not take its move
        yet (`tpyc/mir/test_nested_records.py`, `own_leg`)."""
        if self.holds_reference_leaf(slot):
            return True
        u = unwrap_readonly(unwrap_ref_type(slot))
        if not (isinstance(u, OwnType)
                and has_view_param_form(unwrap_readonly(u.wrapped))):
            return False
        read = peel_value_wrappers(source)
        if read in self.ctx.reread_reads:
            return False
        binding = self.ctx.read_bindings.get(read)
        return binding is not None and binding.kind is ReadKind.LOCAL

    def holds_reference_leaf(self, slot: TpyType) -> bool:
        """Whether an owning slot of type `slot` holds a reference leaf, so
        a source it takes gets a record: a tuple with a reference path, or
        a scalar reference payload -- an open `T` included, whose leaf is
        VALUE under a `T: ValueType` bound."""
        if bare_tuple(slot) is not None:
            return bool(reference_paths(slot))
        return is_reference_leaf(slot)

    def copy_permission(self, t: TpyType) -> LeafPermission:
        """What a record calls a leaf of type `t` an owning sink copies
        implicitly: VALUE for one a `ValueType` bound settles
        (`copy_is_unobservable`), HEDGED for an open payload only an
        instantiation decides (`own_copy.copy_is_hedged`, the copy
        diagnostic's rule), else WARNED."""
        bare = peel_slot(t)
        if self.ctx.copy_is_unobservable(bare):
            return LeafPermission.VALUE
        payload = unwrap_readonly(bare)
        if has_view_param_form(payload):
            # An owned immutable buffer (str, bytes): its copy is not
            # observable.
            return LeafPermission.VALUE
        return (LeafPermission.HEDGED
                if own_copy.copy_is_hedged(payload, payload)
                else LeafPermission.WARNED)

    def _record_paths(self, tt: TupleType,
                      prefix: ElemPath = ()) -> frozenset[ElemPath]:
        """The reference leaf paths of tuple `tt` (at `prefix`) a record
        gives a leaf: every one, a `ValueType`-bounded one (`T`,
        `T | int32`) as VALUE (`copy_permission`)."""
        return reference_paths(tt, prefix)

    def build_source_records(self) -> list[TpyExpr]:
        """The per-leaf record (`source_records`) of every owning sink the
        body registered, built once from the settled facts: each leaf
        borrowed per the source's incoming ownership (`tuple_lent_paths`;
        a scalar's `member_lends`), owned per the slot (`owned_slot_paths`;
        a scalar's `Own` slot), and whether the sink copies it per the
        source facts and the `copy()` nodes; a name read's MOVE is
        `name_moves`; a copied leaf is WARNED or HEDGED by its type
        (`copy_permission`). Returns the keys of the records it published."""
        fs = self.ctx.func
        regs, fs.tuple_sinks = fs.tuple_sinks, IdentityMap()
        keys = [peel_value_wrappers(reg.source) for reg in regs.values()]
        # A literal member is covered by its outermost literal's record
        # (`read_record`), registered before any record is built so which
        # record covers a read does not depend on the order the sinks
        # registered in.
        covered = IdentityMap()
        for reg in regs.values():
            for m, _at in self._literal_members(reg):
                covered[peel_value_wrappers(m)] = True
        own = [reg for reg in regs.values()
               if peel_value_wrappers(reg.source) not in covered
               and peel_value_wrappers(reg.source)
               not in self.ctx.member_sources]
        for reg in own:
            key = peel_value_wrappers(reg.source)
            for m, at in self._literal_members(reg):
                mk = peel_value_wrappers(m)
                assert mk not in self.ctx.source_records, (
                    "a literal member published a record of its own", mk)
                self.ctx.member_sources[mk] = (key, at)
        for reg in own:
            tt = bare_tuple(reg.slot)
            if tt is not None and self.tuple_name_source(reg.source):
                leaves: dict[ElemPath, TupleLeaf] = {}
                self._name_leaves(
                    reg.source, tt, self._record_paths(tt),
                    self._slot_owned_paths(reg), (), leaves,
                    returned=reg.sink is TupleSink.RETURN,
                    lends=reg.sink is TupleSink.UNPACK)
                publish_source_record(self.ctx, reg.source,
                                      SourceRecord(_declared(reg, leaves)))
                continue
            arm_facts = (self._scalar_arm_facts(reg) if tt is None
                         else self._tuple_arm_facts(reg, tt))
            leaves, _lent = arm_facts(reg.source)
            leaves = _declared(reg, leaves)
            publish_source_record(self.ctx, reg.source,
                                  SourceRecord(leaves))
        return keys

    def _publish_copy_argument(self, e: TpyExpr) -> None:
        """The argument of a `copy()` call `e` gets a record copying every
        leaf explicitly: a lowering that peels the `copy()` reads the
        argument, which the call's own record does not reach."""
        arg = self._copy_argument(peel_value_wrappers(e))
        if arg is None:
            return
        key = peel_value_wrappers(arg)
        if (key in self.ctx.source_records
                or key in self.ctx.member_sources):
            return
        publish_source_record(self.ctx, arg, SourceRecord({
            (): TupleLeaf(True, True, LeafPermission.EXPLICIT)}))

    def _publish_body_copy_arguments(self, body: list[TpyStmt]) -> None:
        """Every `copy()` the body spells publishes its argument's EXPLICIT
        record, whatever reads the call (a name binding, an unpack, an
        argument): the lowering that peels the call reads the argument."""
        def visit(e: TpyExpr) -> bool:
            self._publish_copy_argument(e)
            # `children()` stops at a lambda (its own scope), but its
            # result's `copy()` is lowered like the body's.
            if isinstance(e, TpyLambda):
                walk_expr_tree(e.body, visit)
            return True
        walk_body_stmts(body, lambda e: walk_expr_tree(e, visit),
                        lambda _s: None)

    @staticmethod
    def _copy_argument(expr: TpyExpr) -> 'TpyExpr | None':
        """The one argument of a `copy(x)` call, by its resolved callee, so
        the module-qualified spelling (`tpy.copy(x)`) is the same call."""
        if not isinstance(expr, (TpyCall, TpyMethodCall)):
            return None
        fi = expr.resolved_function_info
        if (fi is None or fi.qualified_name != qnames.COPY
                or len(expr.args) != 1 or expr.kwargs):
            return None
        return expr.args[0]

    def _slot_owned_paths(self, reg: SinkRead) -> frozenset[ElemPath]:
        """The leaf paths `reg`'s slot owns: its `Own` markers'
        (`owned_slot_paths`), or -- a local's binding -- what the binding
        owns at its own site (`site_ownership`), a scalar's one leaf
        at `()`."""
        if reg.binds is None:
            if bare_tuple(reg.slot) is not None:
                return owned_slot_paths(reg.slot)
            # Owned storage registers its slot as `Own`
            # (`register_sink`); a result the C++ returns by value in its
            # storage form owns it too (a generator, an iterator object).
            bare = unwrap_readonly(unwrap_ref_type(reg.slot))
            owns = result_owns_storage(bare) and (
                isinstance(bare, OwnType)
                or (reg.sink is TupleSink.RETURN and is_reference_leaf(bare)))
            return frozenset({()}) if owns else frozenset()
        own = site_ownership(self.ctx, reg.binds, bound_name(reg.binds))
        paths = own.owned_paths(reg.slot)
        if bare_tuple(reg.slot) is None:
            return paths & frozenset({()})
        return paths & reference_paths(bare_tuple(reg.slot))

    def _tuple_arm_facts(self, reg: SinkRead, tt: TupleType) -> Callable[
            [TpyExpr], tuple[dict[ElemPath, TupleLeaf], frozenset[ElemPath]]]:
        """The leaves of a tuple source other than a name (or of one arm
        of it) at `reg`'s slot, and the paths it lends."""
        owned = self._slot_owned_paths(reg)

        def facts(src: TpyExpr) -> tuple[dict[ElemPath, TupleLeaf],
                                         frozenset[ElemPath]]:
            leaves: dict[ElemPath, TupleLeaf] = {}
            self._source_leaves(src, tt, owned, (), leaves, reg.sink)
            return leaves, self.tuple_lent_paths(src, tt) or frozenset()
        return facts

    def _scalar_arm_facts(self, reg: SinkRead) -> Callable[
            [TpyExpr], tuple[dict[ElemPath, TupleLeaf], frozenset[ElemPath]]]:
        """A scalar source's one leaf (or one arm's), at path `()`: the
        rule a literal member takes (`_member_leaf`), the slot owning it
        when it is an `Own` slot; and whether it lends. A payload a
        `ValueType` bound settles (`T`, `T | int32`) takes a VALUE leaf
        where it would copy (`copy_permission`)."""
        none: frozenset[ElemPath] = frozenset()
        owns = () in self._slot_owned_paths(reg)
        whole = peel_value_wrappers(reg.source)
        returned = reg.sink is TupleSink.RETURN
        # An element read moves out of its tuple at a sink that takes its
        # source out of the body -- an `Own` slot, a result or an argument
        # (`element_moves_out`); owned storage keeps the tuple's own.
        takes_element = (reg.sink is None
                         or reg.sink in _BORROW_FORM_SINKS)
        payload = peel_slot(reg.slot)

        def facts(src: TpyExpr) -> tuple[dict[ElemPath, TupleLeaf],
                                         frozenset[ElemPath]]:
            alone = peel_value_wrappers(src) is whole
            return ({(): self._member_leaf(
                        src, owns, payload, returned=returned and alone,
                        element_move=takes_element and alone)},
                    frozenset({()}) if self.member_lends(src) else none)
        return facts

    def _name_leaves(self, src: TpyExpr, tt: TupleType,
                     paths: frozenset[ElemPath],
                     owned: frozenset[ElemPath], prefix: ElemPath,
                     out: dict[ElemPath, TupleLeaf], *,
                     returned: bool = False, lends: bool = False) -> None:
        """A tuple NAME read's leaves at `paths` -- read whole at its sink
        or as the literal member at `prefix`, the name read at the
        member's slot, whose owned paths are the parent slot's (`owned`)
        under `prefix`: a leaf the slot does not own passes; one the read
        moves (`name_moves`, at its path below the name; `returned`: the
        whole return of it) moves; every other owned leaf copies (a
        captured name's included: it is read in place), WARNED or HEDGED
        by its type (`tt` the tuple at `prefix`) -- but at an unpack
        (`lends`), whose targets refer to the elements of a source it does
        not consume, where it passes."""
        node = peel_coerce(src)
        for p in paths:
            below = p[len(prefix):]
            if p not in owned:
                out[p] = TupleLeaf(True, False, LeafPermission.PASS)
            elif (isinstance(node, TpyName)
                  and self.name_moves(
                      node, below, returned=returned,
                      deferred_return=self.copy_permission(
                          _element_at(tt, below))
                      is not LeafPermission.VALUE)):
                out[p] = TupleLeaf(True, True, LeafPermission.MOVE)
            elif lends:
                out[p] = TupleLeaf(True, False, LeafPermission.PASS)
            else:
                out[p] = TupleLeaf(True, True, self.copy_permission(
                    _element_at(tt, below)))

    def _source_leaves(self, src: TpyExpr, tt: TupleType,
                       owned: frozenset[ElemPath], prefix: ElemPath,
                       out: dict[ElemPath, TupleLeaf],
                       sink: 'TupleSink | None') -> None:
        """The leaves of a tuple source other than a top-level name, at
        `prefix` of its sink's slot (`tt` the tuple there): a literal per
        member (a nested tuple member by its own source, at
        `nested_member_sink`'s sink, a name by the name rule
        `_name_leaves`), a `copy()` copies every leaf explicitly; every other
        source -- a select included -- copies each owned leaf it lends
        (`tuple_lent_paths`) and moves the rest, borrowed unless the leaf
        is a new value (`new_value`). A select of arms that each
        copy a leaf explicitly copies it explicitly. A copied leaf is
        WARNED or HEDGED by its type (`copy_permission`)."""
        paths = self._record_paths(tt, prefix)
        if prefix and self.tuple_name_source(src):
            self._name_leaves(src, tt, paths, owned, prefix, out)
            return
        if self.is_copy_call(src):
            for p in paths:
                out[p] = TupleLeaf(True, p in owned, LeafPermission.EXPLICIT
                                   if p in owned else LeafPermission.PASS)
            return
        inner = peel_value_wrappers(src)
        if isinstance(inner, TpyTupleLiteral):
            for i, et in enumerate(tt.element_types):
                if i >= len(inner.elements):
                    continue
                m, at = inner.elements[i], prefix + (i,)
                nested = bare_tuple(et)
                if nested is not None:
                    # `whole`: the record filters by the slot's
                    # `owned_slot_paths` itself, so an argument's unmarked
                    # member takes the storage verdict at its owned leaves.
                    self._source_leaves(
                        m, nested, owned, at, out,
                        nested_member_sink(sink, isinstance(et, OwnType),
                                           whole=True))
                elif at in paths:
                    out[at] = self._member_leaf(m, at in owned, et)
            return
        explicit: frozenset[ElemPath] = frozenset()
        if isinstance(inner, TpyIfExpr):
            def arm_explicit(arm: TpyExpr) -> frozenset[ElemPath]:
                arm_leaves: dict[ElemPath, TupleLeaf] = {}
                self._source_leaves(arm, tt, owned, prefix, arm_leaves, sink)
                return frozenset(
                    p for p, leaf in arm_leaves.items()
                    if leaf.permission is LeafPermission.EXPLICIT)
            explicit = self._explicit_arms(inner, arm_explicit)
        lent = self.tuple_lent_paths(src, tt, prefix) or frozenset()
        for p in paths:
            b, o = p in lent, p in owned
            if not o:
                out[p] = TupleLeaf(b, o, LeafPermission.PASS)
            elif p in explicit:
                out[p] = TupleLeaf(True, o, LeafPermission.EXPLICIT)
            elif not b:
                out[p] = TupleLeaf(not self.new_value(src, p[len(prefix):]),
                                   o, LeafPermission.MOVE)
            else:
                out[p] = TupleLeaf(b, o, self.copy_permission(
                    _element_at(tt, p[len(prefix):])))

    def new_value(self, expr: TpyExpr, path: ElemPath = ()) -> bool:
        """Whether the value at element `path` of `expr` is proven NEW --
        no storage the program holds, so a copy of it copies nothing
        CPython would alias: a result that is no borrow (`returns_borrow`;
        a construction included) at a path its call does not lend
        (`tuple_lent_paths`), a literal, a field of a new value (off a call
        only one `call_result_is_fresh` proves) or a constant-index element
        of one, and a select, `and`/`or` or coercion whose every value arm
        is new. An allow-list: every other source -- a name, a walrus (the
        storage of the name it binds, which stays in use), an element or
        field of existing storage, a shape not listed -- is borrowed."""
        inner = peel_coerce(expr)
        if isinstance(inner, TpyIfExpr):
            return (self.new_value(inner.then_expr, path)
                    and self.new_value(inner.else_expr, path))
        if isinstance(inner, TpyBinOp) and inner.op in ("&&", "||"):
            return (self.new_value(inner.left, path)
                    and self.new_value(inner.right, path))
        if isinstance(inner, TpyTupleLiteral):
            if path:
                return (0 <= path[0] < len(inner.elements)
                        and self.new_value(inner.elements[path[0]], path[1:]))
            return all(self.new_value(m) for m in inner.elements)
        if isinstance(inner, TpyFieldAccess):
            obj = peel_coerce(inner.obj)
            return (inner.hidden_call is None
                    and not isinstance(unwrap_qualifiers(
                        self.ctx.get_expr_type(obj)), PtrType)
                    and (not isinstance(obj, TpyCallLike)
                         or call_result_is_fresh(self.ctx, obj, proof=True))
                    and self.new_value(obj))
        if (isinstance(inner, TpySubscript)
                and getitem_call_of(inner) is None):
            idx = const_tuple_index(inner.index)
            return idx is not None and self.new_value(inner.obj,
                                                      (idx,) + path)
        if _produces_result(inner):
            # A call's result at an owning slot is the callee's new value
            # unless it is a borrow (`returns_borrow`: a fresh-stamped
            # result by its lent fact, `copy_observable`); a tuple result's
            # element is unless the call lends it.
            if returns_borrow(self.ctx, inner):
                return False
            t = self.ctx.get_expr_type(inner)
            if path and bare_tuple(t) is not None:
                return not paths_overlap(
                    path, self.tuple_lent_paths(inner, t) or ())
            return True
        return isinstance(inner, _NEW_LITERALS)

    def _literal_members(self, reg: SinkRead
                         ) -> Iterator[tuple[TpyExpr, ElemPath]]:
        """Every tuple-literal member (with its path) a tuple sink's record
        covers: the members `_source_leaves` reads, a nested tuple member's
        and a select arm's included. A check a member's own read ran (a
        move-through claim) records nothing of its own."""
        tt = bare_tuple(reg.slot)
        if tt is None or self.tuple_name_source(reg.source):
            return iter(())
        return self._members_under(reg.source, tt, ())

    def _members_under(self, src: TpyExpr, tt: TupleType, prefix: ElemPath
                       ) -> Iterator[tuple[TpyExpr, ElemPath]]:
        if self.is_copy_call(src) or (prefix and self.tuple_name_source(src)):
            return
        inner = peel_value_wrappers(src)
        if isinstance(inner, TpyIfExpr):
            for arm in (inner.then_expr, inner.else_expr):
                yield from self._members_under(arm, tt, prefix)
            return
        if not isinstance(inner, TpyTupleLiteral):
            return
        for i, et in enumerate(tt.element_types):
            if i >= len(inner.elements):
                continue
            m, at = inner.elements[i], prefix + (i,)
            yield m, at
            nested = bare_tuple(et)
            if nested is not None:
                yield from self._members_under(m, nested, at)

    @staticmethod
    def _explicit_arms(select: TpyIfExpr,
                       explicit_of: Callable[[TpyExpr], frozenset[ElemPath]]
                       ) -> frozenset[ElemPath]:
        """The leaves a select copies explicitly: those every arm copies
        under `copy()` (`explicit_of`, one arm's), at any depth."""
        return (explicit_of(select.then_expr)
                & explicit_of(select.else_expr))

    def _explicit_select(self, m: TpyExpr) -> bool:
        """A scalar select whose one leaf every arm copies under `copy()`
        (`_explicit_arms` at path `()`)."""
        inner = peel_value_wrappers(m)

        def arm_explicit(arm: TpyExpr) -> frozenset[ElemPath]:
            return (frozenset({()}) if self.is_copy_call(arm)
                    or self._explicit_select(arm) else frozenset())
        return (isinstance(inner, TpyIfExpr)
                and () in self._explicit_arms(inner, arm_explicit))

    def _member_leaf(self, m: TpyExpr, slot_owns: bool, payload: TpyType,
                     *, returned: bool = False,
                     element_move: bool = False) -> TupleLeaf:
        """One scalar leaf -- a member of a tuple literal, or a whole
        scalar source (`returned`: the whole return of it) -- from its
        source facts: a borrow the slot does not own passes; an explicit
        `copy()` (a select of them included) copies explicitly; a source
        that does not lend moves, borrowed unless it is a new value
        (`new_value`); a name read that moves (`name_moves`), an element
        the read moves out of its tuple (`element_moves_out`) or a consuming
        method's field moves, borrowed; any other borrowed leaf the slot
        owns is copied, WARNED or HEDGED by its `payload` type
        (`copy_permission`)."""
        lends = self.member_lends(m)
        if not slot_owns:
            return TupleLeaf(lends, False, LeafPermission.PASS)
        if self.is_copy_call(m) or self._explicit_select(m):
            return TupleLeaf(True, True, LeafPermission.EXPLICIT)
        copies = TupleLeaf(True, True, self.copy_permission(payload))
        inner = peel_coerce(m)
        # A name read moves where its binding owns it and the read consumes
        # it, whatever a check told the lowering on the way; over any other
        # source's move a copy a check declared stands.
        if (isinstance(inner, TpyName)
                and self.name_moves(
                    inner, (), returned=returned,
                    deferred_return=copies.permission
                    is not LeafPermission.VALUE)):
            return TupleLeaf(True, True, LeafPermission.MOVE)
        if not lends:
            return TupleLeaf(not self.new_value(m), True, LeafPermission.MOVE)
        # A consuming method's own field moves out of `self`, as its scalar
        # read does (`_check_own_borrowed_source`).
        if ((element_move and self.element_moves_out(inner))
                or (isinstance(inner, TpyFieldAccess)
                    and inner.consuming_move)):
            return TupleLeaf(True, True, LeafPermission.MOVE)
        return copies


    def _derive_owning_storage(self, expr: TpyExpr) -> bool:
        """Whether a binding from `expr` makes the local OWN its tuple
        element storage: an owning-tuple call, a name already carrying the
        fact, or a ternary with an owning arm (the result aliases either,
        so it is hazardous if either is -- matching the fresh-fact merge).
        """
        return any(self.ctx.func.bp_is_owning_storage(inner.name)
                   if isinstance(inner, TpyName)
                   else self._is_owning_tuple_call(inner)
                   for inner in ternary_operands(expr))

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
        operands = ternary_operands(init_expr)
        if len(operands) > 1:
            return sorted({i for inner in operands
                           for i in self._derive_copy_into_own_hazards(inner)})
        inner = operands[0]
        if isinstance(inner, TpyName):
            return sorted(self.copy_into_own_idxs(inner.name))
        fi = (inner.resolved_function_info
              if isinstance(inner, (TpyCall, TpyMethodCall)) else None)
        rt = unwrap_readonly(fi.return_type) if fi is not None else None
        if isinstance(rt, TupleType) and rt.has_ref_elements():
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
        # A hazard derivation: the move it predicts is the tuple capture's
        # record (`_annotate_tuple_elem_capture`).
        if self.is_last_use_move_candidate(elem):
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
        # Owned local at its last use moves into the slot -- not a copy (the
        # tuple capture records that move).
        if self.is_last_use_move_candidate(elem):
            return False
        return True

    def check_name_borrow_into_own(self, name: str, tuple_type: TupleType,
                                   expr: TpyExpr, slot: TpyType,
                                   sink: TupleSink) -> None:
        """Warn when a tuple NAME referenced by `name` feeds a borrowed
        element into an `Own[T]` slot of the contextual `tuple_type` -- the
        implicit copy the scalar `Own[T]` and the literal-tuple forms declare
        the same way -- and record it at `sink`. A name lends every
        reference element; which it copies follows its binding facts
        (`copy_into_own_idxs`), or a parameter's, loop variable's or
        global's declared type; a nested def's capture of its enclosing
        scope (`reads_capture`) is read in place. `slot` is the declared
        slot (an `Own[tuple[...]]` owns nested elements `tuple_type` leaves
        plain)."""
        # An `Own` argument copies a capture whole, as any name its read
        # outlives (the argument's own check declares that copy).
        if sink is not TupleSink.ARG and self.reads_capture(name):
            self.check_captured_tuple_read(name, tuple_type, expr, slot, sink)
            return
        self.register_sink(expr, slot, sink, per_leaf=True)
        # A name the read outlives is copied whole -- the ownership-transfer
        # parameter binds only an rvalue, a yield's frame keeps the name --
        # and the coercion's warning names each element it copies; a return
        # always hands its name over. Only the warning's text is chosen here.
        declared_whole = (sink is not TupleSink.RETURN
                          and not self.is_last_use_move_candidate(expr))
        # Every leaf the slot owns registers its copy; the record says which
        # the read moves. A name a closure pins is copied whole, its copy
        # the return's (`_warn_live_name_return`).
        if not (declared_whole or (sink is TupleSink.RETURN
                                   and self.returned_captured_name(expr)
                                   is not None)):
            for path in sorted(owned_slot_paths(slot)
                               & reference_paths(tuple_type)):
                self._name_leaf_copy(tuple_type, path, expr)
        elif declared_whole and sink is TupleSink.YIELD:
            # The frame keeps the name, so the yield copies each element the
            # step owns, named as the result's element warning names it.
            payloads = dc_replace(tuple_type, element_types=tuple(
                _element_payload(e) for e in tuple_type.element_types))
            for path in sorted(p for p in owned_slot_paths(slot)
                               if len(p) == 1):
                self._warn_tuple_path_copies(
                    payloads, (path,), "owned storage", expr, source=expr,
                    hedge_open=True,
                    nocopy_hint=self.nocopy_hint(expr, path[0], note=""),
                    note_read=expr,
                    note_errors=not self.is_consuming_field_borrow(expr))
        # A movable owned-tuple source at its last use MOVES into the owned
        # sink (forward as arg, return or yield), so it is consumed -- mark it for
        # the unconsumed-param warning. Borrowed/copied sources took the
        # raise/warn paths above (`claim_name_move` is False for them), so they
        # are correctly left unconsumed.
        if isinstance(expr, TpyName) and self.claim_name_move(expr):
            self.check_own_consumption(expr)

    def _name_leaf_copy(self, tt: TupleType, path: ElemPath,
                        expr: TpyExpr) -> None:
        """Register the copy a read of tuple name `expr` into owned storage
        of `tt` makes of the owned leaf at `path` it does not hand over --
        a whole `Own` element as the scalar `T -> Own[T]` does, a deeper
        reference leaf as its tuple path -- with the binding that makes the
        name non-owning there; its record decides whether it stands."""
        i, et = path[0], tt.element_types[path[0]]
        if len(path) == 1 and unwrap_optional_own(et) is not None:
            self._warn_tuple_path_copies(
                tt, (path,), "owned storage", expr, source=expr,
                hedge_open=True, nocopy_hint=self.nocopy_hint(expr, i, note=""),
                note_read=expr,
                note_errors=not self.is_consuming_field_borrow(expr))
        elif len(path) > 1:
            self._warn_tuple_path_copies(
                tt, (path,), "owned storage", expr, source=expr,
                hedge_open=True, note_read=expr)

    def declared_lent_paths(self, name: str,
                            arity: int) -> frozenset[ElemPath]:
        """The element paths a tuple PARAM, LOOP VARIABLE or module GLOBAL
        `name` of `arity` elements lends by its declared type: it carries no
        construction-time facts, so its declared elements say what arrives
        borrowed, at every owned sink. Empty for any other name."""
        func = self.ctx.func
        is_global = self._is_reference_tuple_global(name)
        if not (is_global
                or (name in func.current_param_names
                    and name not in func.current_rebound_params)
                or (name in func.loop_vars
                    and name not in func.current_reassigned_vars)):
            return frozenset()
        declared = (self.ctx.global_scope.lookup(name) if is_global
                    else func.current_scope.lookup(name))
        dt = (unwrap_readonly(unwrap_ref_type(declared))
              if declared is not None else None)
        if not isinstance(dt, TupleType) or len(dt.element_types) != arity:
            return frozenset()
        return reference_paths(dt) - owned_slot_paths(dt)

    def check_captured_tuple_read(self, name: str, tuple_type: TupleType,
                                  expr: TpyExpr, slot: TpyType,
                                  sink: TupleSink) -> None:
        """A closure reading a tuple name it captured into an owning slot at
        `sink` reads the capture in place: every reference leaf arrives
        borrowed -- one its declaration marks `Own` too, which the closure
        never owns -- so the slot copies each leaf it owns, as the scalar
        capture's return does. No read of a capture moves."""
        self.register_sink(expr, slot, sink, per_leaf=True)
        scope = self.ctx.func.current_scope
        dt = bare_tuple(scope.lookup(name) if scope is not None else None)
        if dt is None or len(dt.element_types) != len(tuple_type.element_types):
            return
        owned = owned_slot_paths(slot)
        refs = reference_paths(dt)
        self._warn_tuple_path_copies(
            tuple_type, owned & refs & reference_paths(tuple_type),
            "owned storage", expr, source=expr, hedge_open=True,
            nocopy_hint=NOCOPY_CAPTURE_HINT)

    def _is_reference_tuple_global(self, name: str) -> bool:
        """A module global that is a tuple of pointer slots, read where no
        local shadows it: it owns none of its elements, so an owning slot
        copies every reference element, as the scalar global's does."""
        if name not in self.ctx.top_level_decls:
            return False
        scope = self.ctx.func.current_scope
        while scope is not None and scope is not self.ctx.global_scope:
            if name in scope.bindings:
                return False
            scope = scope.parent
        gt = self.ctx.global_scope.lookup(name)
        return (isinstance(unwrap_readonly(gt), TupleType)
                and global_binds_by_reference(gt))

    def _derive_tuple_member_hazards(
            self, tt: TupleType, expr: TpyExpr) -> frozenset[int]:
        """Fresh-dangle borrow-member hazard indices for an init expr bound
        to a tuple local of type `tt`.

        Dispatches on provenance:
        - tuple literal: scan its elements (`_tuple_literal_fresh_borrow_elems`).
        - bare name: inherit the source local's already-recorded fact, re-checked
          against `tt` so an `Own[T]` boundary element (the escape) is dropped.
        - ternary: UNION the two arms -- the result aliases either, so it is
          hazardous if either is (conservative over-rejection, matching the
          flow_facts branch merge).
        Other init shapes (calls, etc.) carry no fact: a call can only return a
        durable borrow (its own fresh members are rejected at its return), and
        durable members alias safely in pointer borrow form.
        """
        fresh: set[int] = set()
        for inner in ternary_operands(expr):
            if isinstance(inner, TpyName):
                fresh |= {i for i in self.ctx.func.bp_owns_fresh_idxs(
                              inner.name)
                          if self._tuple_elem_still_borrow(tt, i)}
            else:
                fresh |= self._tuple_literal_fresh_borrow_elems(tt, inner)
        return frozenset(fresh)

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
        # A ternary returns whichever arm is taken -- check each.
        for inner in ternary_operands(expr):
            if isinstance(inner, TpyName):
                self._check_tuple_name_member_local(tuple_type, inner, verb)

    def _check_tuple_name_member_local(self, tuple_type: TupleType,
                                       inner: TpyName, verb: str) -> None:
        # An element the boundary still borrows whose source is a fresh
        # value has no storage of its own at either boundary.
        dangling = sorted(i for i in self.ctx.func.bp_owns_fresh_idxs(
                              inner.name)
                          if self._tuple_elem_still_borrow(tuple_type, i))
        if dangling:
            fresh = dangling[0]
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
        ADDRESSES into that storage (codegen's per-element lend), so the
        storage root must outlive the call: non-Own params, non-consuming
        self, and globals qualify; locals, temporaries, Own params, and
        consuming-method self die at exit. Yields are exempt -- generator
        storage is frame-rooted (alive while the consumer iterates) and the
        consumer-side ephemeral-escape checks cover retention.
        """
        if not tuple_type.has_pointer_repr_element():
            return
        # A ternary returns whichever arm is taken -- check each.
        for inner in ternary_operands(expr):
            self._check_tuple_operand_return_root(tuple_type, inner)

    def _check_tuple_operand_return_root(self, tuple_type: TupleType,
                                         inner: TpyExpr) -> None:
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
            if self._tuple_root_is_body_storage(inner.name):
                dangles = True
            elif self._borrow_chain_enters_storage(bt, inner.name):
                # EVERY root must be caller-owned: a re-seated name aliases a
                # function-local on one of its paths and the return would point
                # into it, so one unsafe root dangles the tuple.
                srcs = bt.storage_roots_or_self(inner.name)
                dangles = not all(
                    self._name_is_param_or_global(src.root) for src in srcs)
            else:
                its = self.ctx.loop_var_sources(inner.name)
                if its:
                    # Any source outside param/global storage dies at return.
                    dangles = any(not self._name_is_param_or_global(it.root)
                                  for it in its)
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

    def _tuple_root_is_body_storage(self, name: str) -> bool:
        """Whether some storage tuple name `name` reaches (any path) is the
        body's own -- an owning-call-bound local, an ownership-transfer
        parameter or consuming `self` -- which dies at a return while a
        borrowed element still points into it
        (BUGS.md#tuple-borrow-root-lifetime-mir)."""
        func = self.ctx.func
        # The name's own binding fact counts beside its roots: a local
        # owning on one branch also borrows a field on the other.
        roots = [name] + [k.as_name() for k in
                          func.borrow_tracker.all_storage_through_borrows(
                              name)]
        for root in roots:
            if root is None:
                continue
            if func.bp_is_owning_storage(root):
                return True
            if (root in func.current_param_names
                    and not self._name_is_param_or_global(root)):
                return True
        return False

    @staticmethod
    def _borrow_chain_enters_storage(bt: BorrowTracker, name: str) -> bool:
        """Whether `name`'s borrow chain crosses a storage-entering borrow
        (ELEMENT/FIELD/PTR/ITER) -- i.e. holds addresses INTO a container or
        object rather than a value-copy of another local. ALIAS links are
        followed transitively; chains are acyclic so the walk terminates.
        """
        seen: set[str] = set()
        cur: str | None = name
        # A member path borrows nothing: the chain ends at one.
        while cur is not None and cur not in seen:
            seen.add(cur)
            kind = bt.borrow_kind_of(cur)
            if kind is None:
                return False
            if kind is not BorrowKind.ALIAS:
                return True
            nxt = bt.borrow_source(cur)
            if nxt is None:
                return False
            cur = nxt.as_name()
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
