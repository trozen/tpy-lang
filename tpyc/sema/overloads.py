"""
TurboPython Shared Overload Resolution

Unified type matching and overload resolution used across
operators, calls, methods, and module infrastructure.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from enum import Enum
from collections.abc import Hashable
from typing import TYPE_CHECKING, Callable, NamedTuple

from ..typesys import (
    same_class,
    TpyType, IntLiteralType, FloatLiteralType, FLOAT,
    TypeParamRef, TypeParamKind, FunctionInfo, is_protocol_type, unwrap_readonly,
    PendingStrType, PendingViewType, LiteralType, LiteralTag,
    PendingBytesType, TupleType, UnknownElementType, PendingContainerType,
    NominalType, PtrType, OwnType, ReadonlyType, RefType, CallableType, is_fn_type, VoidType, NoneType,
    OptionalType,
    unwrap_ref_type, strip_own_type_args,
    is_callable_type, is_float_type, is_integer_type, is_any_float_type,
    contains_type_param,
)
from ..type_def_registry import (
    is_fixed_int_type, is_big_int_type, is_bool_type,
    is_str_type, is_bytes_type, is_str_category, is_bytes_category,
    int_traits_of,
)
from ..coercions import resolve_coercion, CoercionContext, int_literal_fits_fixed_int
from ..identity_map import IdentityMap
from .protocols import ProtocolConformanceKind

if TYPE_CHECKING:
    from .type_ops import TypeOperations
    ProtocolChecker = Callable[[TpyType, TpyType], bool]
    ProtocolClassifier = Callable[[TpyType, TpyType], 'ProtocolConformanceKind | None']
    DerefChecker = Callable[[TpyType], TpyType | None]
    # The MRO distance from a subclass to its base, None when the first
    # is no strict subclass of the second.
    SubclassChecker = Callable[[TpyType, TpyType], int | None]


class MatchTier(Enum):
    """Specificity of an argument's overload match.

    The integer ``value`` is the rank (lower = more specific) and doubles as
    an index into the per-tier count array in ``_score``. Not an ``IntEnum``
    so that stray int arithmetic or ``tier == 1`` comparisons don't silently
    type-check.
    """
    EXACT_CONCRETE = 1             # arg_inner == param_inner (or nominal-adjacent: IntLit->FixedInt, NoneType->Void, Callable->Fn, ...)
    EXACT_GENERIC_SHAPE = 2        # generic param with concrete outer shape: list[T] vs list[int32]
    SUBCLASS = 3                   # a subclass argument at a base-class param; cost = MRO distance
    PROTOCOL_EXPLICIT = 4          # protocol conformance via classify_protocol_conformance -> EXPLICIT
    PROTOCOL_STRUCTURAL = 5        # protocol conformance via classify_protocol_conformance -> STRUCTURAL
    GENERIC_PROTOCOL_EXPLICIT = 6  # generic over protocol param, explicit conformance
    GENERIC_PROTOCOL_STRUCTURAL = 7  # generic over protocol param, structural conformance
    GENERIC_WILDCARD = 8           # bare T / unconstrained generic
    # An argument the strict match refuses and a conversion accepts (the
    # conversion stage only). The converted count leads the rank
    # (`OverloadFit.rank`), so this slot never decides between candidates.
    CONVERTED = 9


_NUM_MATCH_TIERS = len(MatchTier)


# MatchTier enum is 1-indexed (EXACT_CONCRETE = 1), so score vectors drop slot 0.
_TIER_COUNTS_SIZE = _NUM_MATCH_TIERS + 1


class OverloadAmbiguityError(Exception):
    """Raised by ``rank_overload_candidates`` (and so ``resolve_overload``)
    when several candidates with distinct signatures tie at the top of the
    ranking, in either stage.

    Callers with source-location context should catch this and re-raise as a
    proper ``SemanticError`` so the user sees the call site, not a bare
    traceback. Uncaught, the exception still carries the tied candidates so
    the default message is actionable.
    """
    def __init__(self, candidates: tuple[FunctionInfo, ...]):
        self.candidates = candidates
        sigs = ", ".join(
            f"{c.name}({', '.join(str(p.type) for p in c.params)})"
            for c in candidates
        )
        super().__init__(f"Ambiguous overload: {sigs}")


def call_resolves_param(overload: FunctionInfo, index: int) -> bool:
    """Whether a call to `overload` resolves its parameter `index` from the
    arguments: one whose type as declared (`overload.root`, before any
    substitution) names one of the callee's own type parameters is
    inferred, so where it is a view (`Iterable[T]`) it decides an undecided
    container argument. Any other parameter is a declared slot -- a
    declared view converts per element and decides nothing -- whether or
    not the callee is generic."""
    declared = overload.root.params
    return (bool(overload.type_params) and index < len(declared)
            and contains_type_param(declared[index].type,
                                    set(overload.type_params)))


def call_resolves_params(overload: FunctionInfo) -> tuple[bool, ...]:
    """`call_resolves_param` for each parameter of `overload`."""
    return tuple(call_resolves_param(overload, i)
                 for i in range(len(overload.params)))


def _always_false_checker(arg: 'TpyType', param: 'TpyType') -> bool:
    return False


def _score(per_arg: tuple[tuple[MatchTier, int], ...]) -> tuple[tuple[int, ...], int]:
    """Score a per-arg tier vector: lower is better.

    Primary key is aggregate tier counts (higher tier-count at the strongest
    tier wins); secondary key is the summed cost -- an integer literal's
    distance from the default width, a subclass argument's MRO distance --
    so same-tier candidates prefer the nearest. Element widening is not
    here: it is its own earlier key (`OverloadFit.rank`).
    """
    counts = [0] * _TIER_COUNTS_SIZE
    total_cost = 0
    for tier, cost in per_arg:
        counts[tier.value] += 1
        total_cost += cost
    return (tuple(-c for c in counts[1:]), total_cost)


def _structural_match(arg: TpyType, param: TpyType) -> bool:
    """Structural match with TypeParamRef as wildcard."""
    if isinstance(param, TypeParamRef):
        return True
    # Unwrap Ref, Own, Readonly from both sides (transparent for matching)
    arg = unwrap_ref_type(arg)
    param = unwrap_ref_type(param)
    if isinstance(param, TypeParamRef):
        return True
    if isinstance(arg, OwnType):
        arg = arg.wrapped
    if isinstance(param, ReadonlyType):
        return _structural_match(unwrap_readonly(arg), param.wrapped)
    arg = unwrap_readonly(arg)
    # Callable -> Fn: structurally compatible callable types
    if isinstance(arg, CallableType) and is_fn_type(param):
        if len(arg.param_types) != len(param.param_types):
            return False
        return (all(_structural_match(a, p) for a, p in zip(arg.param_types, param.param_types))
                and _structural_match(arg.return_type, param.return_type))
    # None literal (NoneType) matches None annotation (VoidType)
    if isinstance(arg, NoneType) and isinstance(param, VoidType):
        return True
    if type(arg) != type(param):
        return False
    # Post-Phase-D all containers/primitives/records are NominalType; type()
    # equality alone passes list[int32] vs set[int32]. Require matching name
    # so structural recursion only fires for same-kind NominalType pairs.
    if isinstance(arg, NominalType) and not same_class(arg, param):
        return False
    # PtrType: readonly arg cannot match mutable param (would drop const)
    if isinstance(arg, PtrType) and isinstance(param, PtrType):
        if arg.is_readonly and not param.is_readonly:
            return False
    arg_inners = list(arg.inner_types())
    param_inners = list(param.inner_types())
    if len(arg_inners) != len(param_inners):
        return False
    if not arg_inners:
        return arg == param
    return all(_structural_match(a, p) for a, p in zip(arg_inners, param_inners))


def _scalar_widening_cost(
    actual: TpyType,
    expected: TpyType,
    default_int_type: TpyType | None = None,
) -> int:
    """Cost of widening ``actual`` to ``expected`` for a single type slot.

    Returns 0 when the types are identical and a positive distance when
    ``actual`` widens to ``expected``. Used as a secondary sort key inside a
    match tier so that e.g. ``list[int32]`` prefers ``Iterable[int32]`` over
    ``Iterable[int64]`` / ``Iterable[int]`` / ``Iterable[float]``.

    A numeric literal ranks from its home: ``IntLiteralType`` as the
    ``default_int_type`` would (so a list of ``int`` literals under
    ``default_int=int32`` scores ``Iterable[int32]`` exactly like
    ``list[int32]`` does), ``FloatLiteralType`` as ``float``. When
    ``default_int_type`` is None, IntLiteral comparisons fall back to the
    generic cross-type distance.
    """
    if actual == expected:
        return 0
    # IntLiteralType and UnknownElementType (empty-list literal): compute cost
    # as if widening from default_int_type. This matches the CPython
    # convention that ``sum([]) == 0`` (int) regardless of which numeric
    # overload is declared first in the stdlib.
    if isinstance(actual, (IntLiteralType, UnknownElementType)):
        if default_int_type is None:
            return 1
        if expected == default_int_type:
            return 0
        return _scalar_widening_cost(default_int_type, expected, default_int_type)
    if isinstance(actual, FloatLiteralType):
        if expected == FLOAT:
            return 0
        return _scalar_widening_cost(FLOAT, expected, default_int_type)
    a_tr = int_traits_of(actual)
    e_tr = int_traits_of(expected)
    if is_big_int_type(actual):
        # From a BigInt (the home of an int literal under
        # `--default-int=BigInt`) the fixed widths are ordered, wider closer
        # and unsigned after signed of a width, then float, then float32.
        if e_tr is not None:
            return 1 + 2 * ((64 - e_tr.bits) // 8) + (0 if e_tr.signed else 1)
        if is_any_float_type(expected):
            return 17 if expected == FLOAT else 18
    if a_tr is not None and e_tr is not None:
        # Fixed-int -> fixed-int: bit-width gap, plus a small sign-flip penalty
        # so e.g. int32 -> int64 beats int32 -> uint64.
        gap = max(0, (e_tr.bits - a_tr.bits)) // 8
        sign_penalty = 1 if a_tr.signed != e_tr.signed else 0
        return max(1, gap) + sign_penalty
    if a_tr is not None and is_big_int_type(expected):
        return 8
    if a_tr is not None and is_any_float_type(expected):
        return 16
    if is_any_float_type(actual) and is_any_float_type(expected):
        return 1
    # Unknown shape -- treat as a single widening step.
    return 1


def _type_args_widening_cost(
    actual: TpyType,
    param: NominalType,
    default_int_type: TpyType | None = None,
    type_ops: 'TypeOperations | None' = None,
) -> int:
    """Aggregate widening cost between the type args ``actual`` instantiates
    protocol ``param`` with and ``param``'s own.

    Caller has already confirmed ``actual`` conforms to ``param``. The
    conformance gives the instantiation (`TypeOperations.protocol_type_args_of`:
    ``dict[int8, int32]`` is an ``Iterable[int8]``); where it gives none,
    the type-arg tuples are walked pairwise when they line up, and for
    single-element protocols ``actual.get_element_type()`` scores
    non-parameterised containers (``bytes``/``bytearray`` ->
    ``Iterable[uint8]``) by their baked-in element type. Returns 0 when no
    type-arg information is available.
    """
    if not isinstance(param, NominalType) or not param.type_args:
        return 0
    if isinstance(actual, PendingContainerType):
        # Ranked as the container it spells, so a dict literal is scored
        # as a dict is, never by its value type.
        actual = actual.spelled(actual.parts())
    actual_args: tuple[TpyType | None, ...] = ()
    conformed = (type_ops.protocol_type_args_of(actual, param)
                 if type_ops is not None else None)
    # A generic user record's declared protocol comes back unsubstituted
    # (`Iterable[T]` for a `Bag[int8]`); it says nothing about the elements.
    if (conformed is not None and len(conformed) == len(param.type_args)
            and not any(a is not None and contains_type_param(a)
                        for a in conformed)):
        actual_args = tuple(conformed)
    elif isinstance(actual, NominalType):
        actual_args = tuple(a for a in actual.type_args if isinstance(a, TpyType))
    elif isinstance(actual, TupleType):
        # Tuples collapse to a protocol over a single element type only when
        # all elements are identical; otherwise element-level widening is
        # not well-defined here.
        if actual.element_types and all(t == actual.element_types[0] for t in actual.element_types):
            actual_args = (actual.element_types[0],)
    # Fallback for single-element protocols where the actual's element type
    # isn't directly visible in type_args -- non-parameterised containers
    # (bytes/bytearray -> uint8) and PendingListType expose it via
    # get_element_type().
    if not actual_args and len(param.type_args) == 1 and hasattr(actual, 'get_element_type'):
        elem = actual.get_element_type()
        if elem is not None:
            actual_args = (elem,)
    if len(actual_args) != len(param.type_args):
        return 0
    total = 0
    for a, p in zip(actual_args, param.type_args):
        if a is None or not isinstance(p, TpyType):
            continue
        total += _scalar_widening_cost(a, p, default_int_type)
    return total


class ContainerLeaf(NamedTuple):
    """One numeric type position of an undecided container argument at an
    overload candidate's parameter: the cell that decides it, None when
    none does (a literal written in the call); the type it holds so far;
    the type the parameter wants there."""
    cell: Hashable | None
    held: TpyType
    wanted: TpyType


class ContainerMeet(NamedTuple):
    """How an undecided container argument meets one candidate's parameter
    (`_undecided_container_arm`): whether a call to that candidate alone
    would accept it, and if so its leaves as the parameter wants them."""
    accepted: bool
    leaves: tuple[ContainerLeaf, ...] = ()


def _undecided_container(arg_type: TpyType) -> bool:
    """Whether `arg_type` is an undecided container argument's (a container
    literal or an unannotated container local)."""
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    return isinstance(arg_inner, PendingContainerType)


def _undecided_container_arm(
    arg_type: TpyType, param_type: TpyType,
    type_ops: 'TypeOperations | None',
    decides_view: bool = False,
) -> ContainerMeet | None:
    """How an undecided container argument (`_undecided_container`) meets a
    parameter that decides it: one that is no protocol and holds no type
    parameter, or -- `decides_view`, a parameter the call resolved
    (`call_resolves_param`) -- a view of it (`Iterable[int64]`). None when
    this is no such pair (another argument, a declared view, which converts
    per element and decides nothing, a parameter still naming a type
    parameter, or no query to ask). Nothing is decided."""
    param_inner = unwrap_ref_type(unwrap_readonly(param_type))
    if isinstance(param_inner, OwnType):
        param_inner = param_inner.wrapped
    if (type_ops is None or type_ops.pending_arg_leaves is None
            or not _undecided_container(arg_type)
            or (is_protocol_type(param_inner) and not decides_view)
            or contains_type_param(param_inner)):
        return None
    leaves = type_ops.pending_arg_leaves(
        arg_type, param_type, is_protocol_type(param_inner))
    if leaves is None:
        return ContainerMeet(False)
    return ContainerMeet(True, tuple(ContainerLeaf(*leaf) for leaf in leaves))


def _container_fit(
    meets: 'list[ContainerMeet | None]',
    default_int_type: TpyType | None,
    type_ops: 'TypeOperations | None' = None,
) -> int | None:
    """A candidate's container widening -- the first key of its rank in
    every pass: the sum over its undecided container arguments' leaves of
    how far its parameters widen them, a cell counted once and cells tied
    both ways (one list under two names) as one. None when the candidate
    is not applicable: an argument it refuses, one cell (one local passed
    twice, two rows of one nested list) wanted at two types, or cells
    wanted at types they cannot hold together, which their owner answers
    (`TypeOperations.pending_wants_fit`: a local rebound to another, a row
    or an element stored from one into another)."""
    wanted: dict[Hashable, TpyType] = {}
    widens: dict[Hashable, int] = {}
    total = 0
    for meet in meets:
        if meet is None:
            continue
        if not meet.accepted:
            return None
        for leaf in meet.leaves:
            cost = _scalar_widening_cost(leaf.held, leaf.wanted,
                                         default_int_type)
            if leaf.cell is None:
                total += cost
            elif leaf.cell not in wanted:
                wanted[leaf.cell] = leaf.wanted
                widens[leaf.cell] = cost
            elif wanted[leaf.cell] != leaf.wanted:
                return None
    if (not wanted or type_ops is None
            or type_ops.pending_wants_fit is None):
        return total + sum(widens.values())
    groups = type_ops.pending_wants_fit(wanted)
    if groups is None:
        return None
    return total + sum(widens[first] for first in set(groups.values()))


def _upcast_distance(arg_type: TpyType, param_type: TpyType,
                     subclass_checker: 'SubclassChecker | None') -> int | None:
    """How far up its MRO an argument converts to its parameter: a
    subclass record at a base record, or a `Ptr` whose pointee does unless
    that would drop a const. None for any other pair. An owned base
    (`Own[Base]`) ranks like a borrowed one, so the variant Python would
    run is picked; the argument check then refuses the slicing move
    (BUGS.md#own-base-subclass-check-bare-record-only for its gaps)."""
    if subclass_checker is None:
        return None
    param_inner = param_type
    while isinstance(param_inner, (RefType, ReadonlyType, OwnType)):
        param_inner = param_inner.wrapped
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    if isinstance(arg_inner, PtrType) and isinstance(param_inner, PtrType):
        if arg_inner.is_readonly and not param_inner.is_readonly:
            return None
        arg_inner = unwrap_readonly(arg_inner.pointee)
        param_inner = unwrap_readonly(param_inner.pointee)
    return subclass_checker(arg_inner, param_inner)


def _coercion_upcast_distance(arg_type: TpyType, param_type: TpyType,
                              subclass_checker: 'SubclassChecker | None'
                              ) -> int | None:
    """`_upcast_distance` in the conversion stage, which also passes a
    subclass record value at a pointer to its base."""
    distance = _upcast_distance(arg_type, param_type, subclass_checker)
    if distance is not None:
        return distance
    param_inner = unwrap_ref_type(unwrap_readonly(param_type))
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(param_inner, PtrType) and not isinstance(arg_inner, PtrType):
        return _upcast_distance(arg_type, param_inner.pointee, subclass_checker)
    return None


def _classify_strict_match(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_classifier: ProtocolClassifier | None = None,
    default_int_type: TpyType | None = None,
    type_ops: 'TypeOperations | None' = None,
    subclass_checker: 'SubclassChecker | None' = None,
) -> tuple[MatchTier, int] | None:
    """Strict (no-coercion) match with specificity tier + widening cost.

    Returns ``(tier, cost)`` on success, ``None`` otherwise. ``cost`` is 0
    for exact element-type matches and grows with widening distance (used as
    a secondary sort key by ``resolve_overload_result``).
    """
    # Unwrap ReadonlyType, OwnType, RefType -- these are ownership/ref
    # qualifiers transparent for overload matching.
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    param_inner = unwrap_ref_type(unwrap_readonly(param_type))
    if isinstance(param_inner, OwnType):
        param_inner = param_inner.wrapped
    if arg_inner == param_inner:
        return (MatchTier.EXACT_CONCRETE, 0)
    # Every container a call to this candidate alone would accept is a
    # strict match; how far it widens is the candidate's own first key
    # (`_container_fit`), so it costs nothing here.
    meet = _undecided_container_arm(arg_type, param_type, type_ops)
    if meet is not None:
        return (MatchTier.EXACT_CONCRETE, 0) if meet.accepted else None
    # CallableType (Fn and Callable): compare with qualifier unwrapping on inner
    # types. The arg callable may have Own/Ref on param/return types from FI,
    # while the resolved overload's callable has bare types from substitution.
    if (is_callable_type(arg_inner)
            and is_callable_type(param_inner)
            and len(arg_inner.param_types) == len(param_inner.param_types)):
        def _strip(t: TpyType) -> TpyType:
            t = unwrap_ref_type(t)
            if isinstance(t, OwnType): t = t.wrapped
            if isinstance(t, PendingViewType): t = t.family.owned_type
            return t
        if (all(_strip(a) == _strip(p) for a, p in zip(arg_inner.param_types, param_inner.param_types))
                and _strip(arg_inner.return_type) == _strip(param_inner.return_type)):
            return (MatchTier.EXACT_CONCRETE, 0)
    # A numeric literal matches strictly at its home (`_scalar_widening_cost`
    # 0: the default int width, `float` for a float literal) and an int
    # literal at any fixed width it fits, so range(stop: int32) takes
    # IntLiteralType(5) in the first pass. The cost is the distance from
    # the home, so str(IntLiteralType) picks the default-width overload
    # (int32) over the smallest-fitting one (int8).
    if isinstance(arg_inner, (IntLiteralType, FloatLiteralType)):
        cost = _scalar_widening_cost(arg_inner, param_inner, default_int_type)
        if is_fixed_int_type(param_inner):
            if (isinstance(arg_inner, IntLiteralType)
                    and int_literal_fits_fixed_int(arg_inner, param_inner)):
                return (MatchTier.EXACT_CONCRETE, cost)
        elif cost == 0:
            return (MatchTier.EXACT_CONCRETE, 0)
    # None literal (NoneType) matches None type annotation (VoidType)
    if isinstance(arg_inner, NoneType) and isinstance(param_inner, VoidType):
        return (MatchTier.EXACT_CONCRETE, 0)
    # T -> Optional[T] and None -> Optional[T]
    if isinstance(param_inner, OptionalType):
        if isinstance(arg_inner, NoneType):
            return (MatchTier.EXACT_CONCRETE, 0)
        return _classify_strict_match(arg_inner, param_inner.inner, protocol_classifier, default_int_type,
                                      type_ops, subclass_checker)
    # Callable -> Fn: std::function satisfies template requires clauses
    if (isinstance(arg_inner, CallableType) and is_fn_type(param_inner)
            and arg_inner.param_types == param_inner.param_types
            and (arg_inner.return_type == param_inner.return_type
                 or isinstance(param_inner.return_type, VoidType))):
        return (MatchTier.EXACT_CONCRETE, 0)
    # PendingViewType: same-family pending types match each other and their resolved types
    if isinstance(arg_inner, PendingViewType) and isinstance(param_inner, PendingViewType):
        if arg_inner.family is param_inner.family:
            return (MatchTier.EXACT_CONCRETE, 0)
    if isinstance(arg_inner, PendingStrType) and is_str_type(param_inner):
        return (MatchTier.EXACT_CONCRETE, 0)
    # Single-value LiteralType matches multi-value LiteralType if value is in the set.
    # LiteralType -> base type is left to the conversion stage (a converted cell)
    # so that Literal stubs are preferred over plain stubs regardless of order.
    if isinstance(arg_inner, LiteralType) and isinstance(param_inner, LiteralType):
        return ((MatchTier.EXACT_CONCRETE, 0)
                if all(v in param_inner.values for v in arg_inner.values) else None)
    # IntLiteralType matches LiteralType with int base if value is in the set.
    if isinstance(arg_inner, IntLiteralType) and isinstance(param_inner, LiteralType) and param_inner.is_int_base():
        if arg_inner.value is not None and param_inner.contains(LiteralTag.INT, arg_inner.value):
            return (MatchTier.EXACT_CONCRETE, 0)
        return None
    # PendingBytesType (unresolved bytes local) matches bytes params
    if isinstance(arg_inner, PendingBytesType) and is_bytes_type(param_inner):
        return (MatchTier.EXACT_CONCRETE, 0)
    if protocol_classifier is not None and is_protocol_type(param_inner):
        # Unwrap Own[T] from arg -- copy()-wrapped return values should still match
        # plain protocol params (Own is a caller-side ownership marker, not a new type).
        check_arg = arg_inner.wrapped if isinstance(arg_inner, OwnType) else arg_inner
        # Strip Own[T] wrappers from protocol type args so that e.g.
        # list[Node] matches Iterable[Own[Node]] for protocol conformance.
        check_param = param_inner
        if (isinstance(param_inner, NominalType) and param_inner.type_args
                and any(isinstance(a, OwnType) for a in param_inner.type_args)):
            stripped = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_inner.type_args)
            check_param = dc_replace(param_inner, type_args=stripped)
        kind = protocol_classifier(check_arg, check_param)
        if kind is ProtocolConformanceKind.EXPLICIT:
            return (MatchTier.PROTOCOL_EXPLICIT,
                    _type_args_widening_cost(check_arg, check_param, default_int_type,
                                             type_ops))
        if kind is ProtocolConformanceKind.STRUCTURAL:
            return (MatchTier.PROTOCOL_STRUCTURAL, 0)
        return None
    # Non-protocol NominalType with Own[T] in type args: strip Own for overload matching.
    # Applies to any concrete container (e.g. dict[K, Own[V]]) not just DictType.
    # Copy warnings are emitted later by check_type_compatible.
    if (isinstance(param_inner, NominalType) and not is_protocol_type(param_inner)
            and any(isinstance(a, OwnType) for a in param_inner.inner_types())):
        stripped_param = strip_own_type_args(param_inner)
        return (MatchTier.EXACT_CONCRETE, 0) if arg_inner == stripped_param else None
    distance = _upcast_distance(arg_type, param_type, subclass_checker)
    if distance is not None:
        return (MatchTier.SUBCLASS, distance)
    return None


def _bool_checker_to_classifier(
    protocol_checker: ProtocolChecker | None,
) -> ProtocolClassifier | None:
    """Wrap a bool ``protocol_checker`` as a classifier.

    Callers outside ``resolve_overload`` only have the bool API; conservatively
    report STRUCTURAL on a hit so tier info is never fabricated as EXPLICIT.
    """
    if protocol_checker is None:
        return None
    def _adapter(arg: TpyType, param: TpyType) -> ProtocolConformanceKind | None:
        return ProtocolConformanceKind.STRUCTURAL if protocol_checker(arg, param) else None
    return _adapter


def type_matches_strict(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_checker: ProtocolChecker | None = None,
    type_ops: 'TypeOperations | None' = None,
) -> bool:
    """Strict type matching: exact equality or protocol conformance.

    Used for first-pass overload resolution where no coercions are desired.
    """
    result = _classify_strict_match(
        arg_type, param_type, _bool_checker_to_classifier(protocol_checker),
        type_ops=type_ops,
    )
    return result is not None


def _declared_tier(declared: TpyType,
                   instantiated: tuple[MatchTier, int]) -> tuple[MatchTier, int]:
    """The tier of a parameter declared with the callee's own type
    parameter, by the declared shape: a bare `T` is the wildcard, a protocol
    over `T` ranks as a generic protocol, any other shape (`list[T]`) as a
    generic shape. `instantiated` is the argument's match at the
    instantiated parameter, which decides applicability and the cost."""
    inspect = declared
    while isinstance(inspect, (RefType, ReadonlyType, OwnType)):
        inspect = inspect.wrapped
    tier, cost = instantiated
    if isinstance(inspect, TypeParamRef):
        return (MatchTier.GENERIC_WILDCARD, 0)
    if is_protocol_type(inspect):
        # An argument that is the instantiated protocol itself conforms
        # explicitly, as the classifier answers for it.
        if tier in (MatchTier.EXACT_CONCRETE, MatchTier.PROTOCOL_EXPLICIT):
            return (MatchTier.GENERIC_PROTOCOL_EXPLICIT, cost)
        return (MatchTier.GENERIC_PROTOCOL_STRUCTURAL, 0)
    return (MatchTier.EXACT_GENERIC_SHAPE, 0)


def _classify_generic_param_match(
    param_type: TpyType,
    arg_type: TpyType,
    inferred: dict[str, TpyType],
    type_ops: 'TypeOperations',
    protocol_classifier: ProtocolClassifier | None,
    default_int_type: TpyType | None = None,
) -> tuple[MatchTier, int]:
    """Tier + widening cost for a generic-overload param with ``TypeParamRef``,
    by its declared shape (`_declared_tier`); inference has already
    accepted the argument."""
    instantiated: tuple[MatchTier, int] = (MatchTier.EXACT_CONCRETE, 0)
    inspect = param_type
    while isinstance(inspect, (RefType, ReadonlyType, OwnType)):
        inspect = inspect.wrapped
    if is_protocol_type(inspect) and contains_type_param(inspect):
        instantiated = (MatchTier.PROTOCOL_STRUCTURAL, 0)
        substituted = type_ops.substitute_types(inspect, inferred)
        check_arg = unwrap_ref_type(unwrap_readonly(arg_type))
        if isinstance(check_arg, OwnType):
            check_arg = check_arg.wrapped
        if protocol_classifier is not None and is_protocol_type(substituted):
            kind = protocol_classifier(check_arg, substituted)
            if kind is ProtocolConformanceKind.EXPLICIT:
                instantiated = (MatchTier.PROTOCOL_EXPLICIT,
                                _type_args_widening_cost(check_arg, substituted,
                                                         default_int_type, type_ops))
    return _declared_tier(param_type, instantiated)


def type_matches_numeric(
    arg_type: TpyType,
    param_type: TpyType,
) -> bool:
    """Type matching for numeric operators and constructors.

    Handles IntLiteralType and TypeParamRef(INT) flexibility without
    triggering general type coercions (e.g., int32->BigInt promotion).

    - Exact equality
    - IntLiteralType matches IntLiteralType, any fixed-width int, BigInt, float, or float32
    - INT TypeParamRef matches any fixed-width int or BigInt
    """
    if arg_type == param_type:
        return True
    if isinstance(arg_type, IntLiteralType):
        if is_fixed_int_type(param_type):
            return int_literal_fits_fixed_int(arg_type, param_type)
        if is_big_int_type(param_type) or isinstance(param_type, IntLiteralType):
            return True
        if is_float_type(param_type):
            return True
    if isinstance(arg_type, FloatLiteralType):
        if is_any_float_type(param_type):
            return True
    if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
        if is_integer_type(param_type):
            return True
    # T -> Optional[T]: unwrap Optional param and match inner type
    if isinstance(param_type, OptionalType):
        return type_matches_numeric(arg_type, param_type.inner)
    # Recursive container matching: e.g. make_list(IntLiteralType) vs make_list(int32).
    # Post-Phase-D all builtin containers share the NominalType class, so also
    # require matching name (list vs set etc. would otherwise both pass type()
    # equality and fall into the element-only check).
    if type(arg_type) == type(param_type):
        if isinstance(arg_type, NominalType):
            if arg_type.name != param_type.name:
                return False
        arg_elem = arg_type.get_element_type()
        param_elem = param_type.get_element_type()
        if arg_elem is not None and param_elem is not None:
            return type_matches_numeric(arg_elem, param_elem)
    return False


def type_matches_with_coercion(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
    subclass_checker: SubclassChecker | None = None,
    type_ops: 'TypeOperations | None' = None,
) -> bool:
    """Type matching allowing IntLiteral flexibility, protocols, and registered coercions.

    The applicability check of the conversion stage of overload resolution
    (`_converted_cell`), which hands it the argument's conversion target
    (`_conversion_target`), so an Optional parameter arrives here as its
    payload for a non-optional argument.
    """
    # An undecided container is applicable here exactly when it is in the
    # strict pass (`_undecided_container_arm`).
    meet = _undecided_container_arm(arg_type, param_type, type_ops)
    if meet is not None:
        return meet.accepted
    # Unwrap ReadonlyType, OwnType, RefType from args -- mutable values match
    # readonly params, Own[T] variables match T params, and Ref[T] is transparent.
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    param_inner = unwrap_ref_type(unwrap_readonly(param_type))
    # Own[T] param accepts T values (ownership marker, not a distinct type)
    if isinstance(param_inner, OwnType):
        param_inner = param_inner.wrapped
    if type_matches_numeric(arg_inner, param_inner):
        return True
    # None -> Optional[T]
    if isinstance(arg_inner, NoneType) and isinstance(param_inner, OptionalType):
        return True
    # PendingStrType matches any string type (str, String, StrView)
    if isinstance(arg_inner, PendingStrType) and is_str_category(param_inner):
        return True
    # Single-value LiteralType matches multi-value LiteralType if value is in the set
    if isinstance(arg_inner, LiteralType) and isinstance(param_inner, LiteralType):
        return all(v in param_inner.values for v in arg_inner.values)
    # LiteralType falls back to matching its base type
    if isinstance(arg_inner, LiteralType):
        if arg_inner.is_str_base() and is_str_category(param_inner):
            return True
        if arg_inner.is_int_base() and is_integer_type(param_inner):
            return True
        if arg_inner.is_bool_base() and is_bool_type(param_inner):
            return True
    # IntLiteralType matches LiteralType with int base if value is in the set
    if isinstance(arg_inner, IntLiteralType) and isinstance(param_inner, LiteralType) and param_inner.is_int_base():
        if arg_inner.value is not None:
            return param_inner.contains(LiteralTag.INT, arg_inner.value)
        return False
    # PendingBytesType matches any bytes type (bytes, bytearray, BytesView)
    if isinstance(arg_inner, PendingBytesType) and is_bytes_category(param_inner):
        return True
    if protocol_checker and is_protocol_type(param_inner):
        # Unwrap Own[T] -- ownership marker, not a distinct type.
        # Mirrors the unwrapping in type_matches_strict.
        check_arg = arg_inner.wrapped if isinstance(arg_inner, OwnType) else arg_inner
        return protocol_checker(check_arg, param_inner)
    # Callable -> Fn: std::function satisfies template requires clauses
    if (isinstance(arg_inner, CallableType) and is_fn_type(param_inner)
            and arg_inner.param_types == param_inner.param_types
            and (arg_inner.return_type == param_inner.return_type
                 or isinstance(param_inner.return_type, VoidType))):
        return True
    if resolve_coercion(arg_inner, param_inner, CoercionContext.ARG) is not None:
        return True
    if deref_checker:
        deref_target = deref_checker(arg_inner)
        if deref_target is not None and deref_target == param_inner:
            return True
    if _coercion_upcast_distance(arg_type, param_type, subclass_checker) is not None:
        return True
    # Non-protocol NominalType with Own[T] in type args: strip Own for overload matching.
    # Symmetric with the same block in type_matches_strict.
    if (isinstance(param_inner, NominalType) and not is_protocol_type(param_inner)
            and any(isinstance(a, OwnType) for a in param_inner.inner_types())):
        stripped_param = strip_own_type_args(param_inner)
        return arg_inner == stripped_param
    return False


def _numeric_scalar(t: TpyType) -> bool:
    return (int_traits_of(t) is not None or is_big_int_type(t)
            or isinstance(t, IntLiteralType) or is_any_float_type(t))


def _conversion_target(arg_inner: TpyType, param_type: TpyType) -> TpyType:
    """The type an argument converts to at `param_type`: the parameter
    without its qualifiers, and an optional one's payload unless the
    argument is optional or None itself."""
    def _bare(t: TpyType) -> TpyType:
        t = unwrap_ref_type(unwrap_readonly(t))
        return t.wrapped if isinstance(t, OwnType) else t
    target = _bare(param_type)
    if (isinstance(target, OptionalType)
            and not isinstance(arg_inner, (OptionalType, NoneType))):
        # The payload carries its own qualifiers (`Own[float32] | None`).
        target = _bare(target.inner)
    return target


class _Conversion(NamedTuple):
    """How one argument converts to its parameter (`_converted_cell`)."""
    narrowing: int
    widening: int
    cost: int


def _converted_cell(
    arg_type: TpyType, param_type: TpyType,
    protocol_checker: ProtocolChecker | None,
    deref_checker: DerefChecker | None,
    subclass_checker: SubclassChecker | None,
    default_int_type: TpyType | None,
) -> _Conversion | None:
    """How an argument the strict match refuses converts to its parameter,
    None when it does not: whether it narrows (a BigInt into a fixed
    width), how far a numeric scalar widens (`_scalar_widening_cost`, a
    numeric literal from its home), and its cost -- a subclass's distance
    from its base (`_coercion_upcast_distance`). A literal its conversion row's
    `check_range` refuses (an int literal a fixed width cannot hold, a
    float literal float32 cannot) is no conversion there: the call would
    reject it after the pick."""
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    target = _conversion_target(arg_inner, param_type)
    row = resolve_coercion(arg_inner, target, CoercionContext.ARG)
    if (row is not None and row.check_range is not None
            and not row.check_range(arg_inner, target)):
        return None
    if not type_matches_with_coercion(arg_type, target, protocol_checker,
                                      deref_checker, subclass_checker):
        return None
    narrowing = int(is_big_int_type(arg_inner) and is_fixed_int_type(target))
    widening = 0
    if (not is_big_int_type(arg_inner)
            and _numeric_scalar(arg_inner) and _numeric_scalar(target)):
        widening = _scalar_widening_cost(arg_inner, target, default_int_type)
    cost = _coercion_upcast_distance(arg_type, target, subclass_checker) or 0
    return _Conversion(narrowing, widening, cost)


def _expand_arg_types_with_kwargs(
    arg_types: list[TpyType],
    kwarg_types: dict[str, TpyType],
    overload: FunctionInfo,
) -> list[TpyType] | None:
    """Expand positional arg_types by inserting kwarg types at their param slots.

    Mirrors ``resolve_kwargs`` index-mapping logic but operates on types so
    ``resolve_overload`` can score kwargs-disambiguated candidates. Returns
    ``None`` when the overload cannot accept the given kwargs (unknown name,
    duplicate of a positional, or missing required param); the overload is
    then rejected at the candidate level.

    Defaulted positional gaps between the last positional arg and the rightmost
    kwarg are filled with the param's own type -- trivially matches itself and
    does not skew cross-overload scoring.
    """
    name_to_index = {p.name: i for i, p in enumerate(overload.params) if not p.is_variadic}

    # One pass over kwargs: validate name, reject positional collisions,
    # and track the rightmost explicitly-provided slot.
    rightmost = len(arg_types) - 1
    for kw_name in kwarg_types:
        idx = name_to_index.get(kw_name)
        if idx is None:
            return None
        if idx < len(arg_types) and not overload.params[idx].keyword_only:
            return None
        if idx > rightmost:
            rightmost = idx

    # Variadic: positional args feed *args; kwargs feed kwonly slots.
    # Append kwonly types in declaration order so _classify_overload can
    # score them at the right positions. Defensive: the parser currently
    # blocks `*args` on `@overload` (parser.py raises "*args is not supported
    # on @overload stubs"), so this branch is unreachable from user code
    # today. Kept correct in case that gate ever opens.
    if overload.has_variadic:
        result = list(arg_types)
        for p in overload.params:
            if not p.keyword_only:
                continue
            if p.name in kwarg_types:
                result.append(kwarg_types[p.name])
            elif not p.has_default:
                return None
        return result

    result: list[TpyType] = []
    for i in range(rightmost + 1):
        p = overload.params[i]
        if i < len(arg_types):
            result.append(arg_types[i])
        elif p.name in kwarg_types:
            result.append(kwarg_types[p.name])
        elif p.has_default:
            result.append(p.type)
        else:
            return None

    # Required kwonly beyond rightmost must be in kwargs
    for i, p in enumerate(overload.params):
        if i > rightmost and p.keyword_only and not p.has_default and p.name not in kwarg_types:
            return None

    return result


class OverloadFit(NamedTuple):
    """A candidate's match (`_classify_overload`): its container widening
    (`_container_fit`), how far the call narrows a scalar (a BigInt into a
    fixed-width parameter), how far its arguments' conversions widen them
    (the elements an explicit protocol match widens, a converted numeric
    scalar), and one cell per argument -- its tier (`CONVERTED` for one
    only a conversion accepts) with its cost (`_score`)."""
    widening: int
    narrowing: int
    conversion_widening: int
    per_arg: tuple[tuple[MatchTier, int], ...]

    @property
    def converted(self) -> int:
        """How many arguments only a conversion accepts; a strict match
        converts none."""
        return sum(1 for tier, _ in self.per_arg if tier is MatchTier.CONVERTED)

    def rank(self) -> tuple[int, int, int, int, tuple[int, ...], int]:
        """Lower is better, one key for both stages: the fewest converted
        arguments, then the container widening -- an overload never
        widens a container another applicable one takes as it is -- the
        least narrowing, the least conversion widening, then the tier
        counts and the summed cost (`_score`). A strict match converts and
        narrows nothing, so the strict stage ranks on the rest."""
        return (self.converted, self.widening, self.narrowing,
                self.conversion_widening, *_score(self.per_arg))


def _has_type_param_params(overload: FunctionInfo) -> bool:
    """Whether `overload` still names its own type parameters in its
    parameters (a generic not yet instantiated by the call site)."""
    return overload.is_generic() and any(
        contains_type_param(p.type) for p in overload.params)


def _classify_overload(
    overload: FunctionInfo,
    arg_types: list[TpyType],
    protocol_checker: ProtocolChecker | None,
    classifier: ProtocolClassifier | None,
    default_int_type: TpyType | None,
    type_ops: 'TypeOperations | None',
    kwarg_types: dict[str, TpyType] | None = None,
    joined_out: 'IdentityMap[FunctionInfo, dict[str, TpyType]] | None' = None,
    subclass_checker: 'SubclassChecker | None' = None,
    convert: bool = False,
    deref_checker: 'DerefChecker | None' = None,
    binding: dict[str, TpyType] | None = None,
) -> OverloadFit | None:
    """Classify every arg against ``overload``'s params, returning its fit
    or ``None`` if any arg rejects (or the container arguments do not fit
    it together).

    Strict (``convert`` off): for a generic overload (``TypeParamRef`` in
    params) inference runs first (using ``protocol_checker`` to validate
    type-parameter bounds) and ``_classify_generic_param_match`` gives the
    TPR params their cells; one refused for a joined fixed-int binding
    leaves its bindings in ``joined_out`` for the conversion stage.

    ``convert`` on: an argument the strict match refuses takes a
    ``CONVERTED`` cell where a conversion accepts it (`_converted_cell`).
    Parameters are taken as given -- at ``binding``, a joined fixed-int
    binding, when there is one -- with no inference.
    """
    if kwarg_types:
        expanded = _expand_arg_types_with_kwargs(arg_types, kwarg_types, overload)
        if expanded is None:
            return None
        arg_types = expanded
    if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
        return None
    params = overload.params
    if convert and binding is not None:
        assert type_ops is not None
        params = type_ops.substitute_method_type_params(overload, binding).params
    has_tpr = not convert and _has_type_param_params(overload)
    inferred: dict[str, TpyType] | None = None
    if has_tpr:
        if type_ops is None:
            return None
        joined: set[str] = set()
        inferred = type_ops.infer_type_params_for_function(
            overload, arg_types, protocol_checker or _always_false_checker,
            joined=joined,
        )
        # A joined fixed-int binding converts an argument, so it is no
        # strict match; the conversion stage ranks it at its instantiation.
        if inferred is not None and joined and joined_out is not None:
            joined_out[overload] = inferred
        if inferred is None or joined:
            return None
    per_arg: list[tuple[MatchTier, int]] = []
    meets: list[ContainerMeet | None] = []
    narrowing = 0
    conversion_widening = 0
    for i, (arg_t, param) in enumerate(zip(arg_types, params)):
        ptype = param.type
        resolved = ptype
        if (has_tpr and _undecided_container(arg_t)
                and contains_type_param(ptype)):
            resolved = type_ops.substitute_types(ptype, inferred)
        meet = (_undecided_container_arm(arg_t, resolved, type_ops,
                                         call_resolves_param(overload, i))
                if _undecided_container(arg_t) else None)
        meets.append(meet)
        # An instantiation a call site substituted before ranking: the
        # declared parameter, not the instantiated one, gives the tier.
        declared = (overload.root.params[i].type
                    if not has_tpr and call_resolves_param(overload, i)
                    else None)
        if meet is not None and not is_protocol_type(
                unwrap_ref_type(unwrap_readonly(resolved))):
            if not meet.accepted:
                return None
            cell = (MatchTier.EXACT_CONCRETE if resolved is ptype
                    else MatchTier.EXACT_GENERIC_SHAPE, 0)
            if declared is not None:
                cell = _declared_tier(declared, cell)
        elif has_tpr and contains_type_param(ptype):
            cell = _classify_generic_param_match(
                ptype, arg_t, inferred, type_ops, classifier, default_int_type,
            )
        else:
            cell = _classify_strict_match(arg_t, ptype, classifier, default_int_type,
                                          type_ops, subclass_checker)
            if cell is not None and declared is not None:
                cell = _declared_tier(declared, cell)
        if cell is None and convert:
            conversion = _converted_cell(arg_t, ptype, protocol_checker,
                                         deref_checker, subclass_checker,
                                         default_int_type)
            if conversion is not None:
                narrowing += conversion.narrowing
                conversion_widening += conversion.widening
                cell = (MatchTier.CONVERTED, conversion.cost)
        if cell is None:
            return None
        # A protocol match that widens the argument's elements converts
        # it: ranked with the container widening, before any tier. An
        # argument with a container meet has its widening counted there.
        if cell[0] in (MatchTier.PROTOCOL_EXPLICIT,
                       MatchTier.GENERIC_PROTOCOL_EXPLICIT):
            if meet is None:
                conversion_widening += cell[1]
            cell = (cell[0], 0)
        per_arg.append(cell)
    widening = _container_fit(meets, default_int_type, type_ops)
    if widening is None:
        return None
    return OverloadFit(widening, narrowing, conversion_widening, tuple(per_arg))


class OverloadCandidate(NamedTuple):
    """One candidate of an overloaded call: the signature and the argument
    types the call has at it (a caller that types arguments per candidate
    passes each its own)."""
    overload: FunctionInfo
    arg_types: list[TpyType]
    kwarg_types: dict[str, TpyType] | None = None


class OverloadPick(NamedTuple):
    """`rank_overload_candidates`' answer: the winning candidate's index,
    None when none is applicable, and the candidates the strict stage
    found applicable (empty when it found none)."""
    winner: int | None
    strict: tuple[int, ...] = ()


def _pick_best(candidates: list[OverloadCandidate],
               ranked: list[tuple[tuple, int]]) -> int:
    """The tie rule of every overloaded call: the best-ranked of the
    applicable `(rank, index)` pairs. Candidates with one parameter-type
    sequence match identically (a substituted generic and its concrete
    twin), so the first of them stands for all; a top tie between distinct
    signatures is refused in either stage (`OverloadAmbiguityError`)."""
    ranked = sorted(ranked, key=lambda r: r[0])
    best = ranked[0][0]
    seen: set[tuple[TpyType, ...]] = set()
    tied: list[int] = []
    for score, index in ranked:
        if score != best:
            break
        key = tuple(p.type for p in candidates[index].overload.params)
        if key not in seen:
            seen.add(key)
            tied.append(index)
    if len(tied) > 1:
        raise OverloadAmbiguityError(
            tuple(candidates[i].overload for i in tied))
    return tied[0]


def rank_overload_candidates(
    candidates: list[OverloadCandidate],
    protocol_checker: ProtocolChecker | None = None,
    classifier: ProtocolClassifier | None = None,
    deref_checker: DerefChecker | None = None,
    default_int_type: TpyType | None = None,
    subclass_checker: SubclassChecker | None = None,
    type_ops: 'TypeOperations | None' = None,
) -> OverloadPick:
    """Two-stage overload resolution over one ranking (`OverloadFit.rank`).

    The strict stage takes the candidates that convert no argument; when
    none does, a generic overload the legacy structural match accepts
    (``type_ops`` omitted: first match in declaration order); then the
    conversion stage ranks every candidate a conversion makes applicable,
    a generic refused for a joined fixed-int binding at its instantiation.
    A non-generic candidate is classified once for both stages.
    """
    joined: IdentityMap[FunctionInfo, dict[str, TpyType]] = IdentityMap()
    fallback: int | None = None
    fits: list[OverloadFit | None] = []
    for index, cand in enumerate(candidates):
        overload = cand.overload
        generic = _has_type_param_params(overload)
        if generic and type_ops is None:
            # Legacy structural-match fallback for generic overloads when the
            # caller hasn't plumbed type_ops (e.g. the error-message-preservation
            # path in calls.py). Kwargs aren't supported on this fallback path
            # because it uses raw structural matching without arity expansion.
            if (fallback is None and not cand.kwarg_types
                    and overload.min_args <= len(cand.arg_types) <= overload.max_args
                    and all(_structural_match(arg_t, p.type)
                            for arg_t, p in zip(cand.arg_types, overload.params))):
                fallback = index
            fits.append(None)
            continue
        fits.append(_classify_overload(
            overload, cand.arg_types, protocol_checker, classifier,
            default_int_type, type_ops, kwarg_types=cand.kwarg_types,
            joined_out=joined, subclass_checker=subclass_checker,
            convert=not generic, deref_checker=deref_checker,
        ))
    strict = [i for i, fit in enumerate(fits)
              if fit is not None and fit.converted == 0]
    if strict:
        return OverloadPick(
            _pick_best(candidates, [(fits[i].rank(), i) for i in strict]),
            tuple(strict))
    if fallback is not None:
        return OverloadPick(fallback)
    ranked: list[tuple[tuple, int]] = []
    for index, cand in enumerate(candidates):
        fit = fits[index]
        if _has_type_param_params(cand.overload):
            fit = _classify_overload(
                cand.overload, cand.arg_types, protocol_checker, classifier,
                default_int_type, type_ops, kwarg_types=cand.kwarg_types,
                subclass_checker=subclass_checker, convert=True,
                deref_checker=deref_checker,
                binding=joined.get(cand.overload),
            )
        if fit is not None:
            ranked.append((fit.rank(), index))
    if not ranked:
        return OverloadPick(None)
    return OverloadPick(_pick_best(candidates, ranked))


def resolve_overload(
    overloads: list[FunctionInfo],
    arg_types: list[TpyType],
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
    default_int_type: TpyType | None = None,
    subclass_checker: SubclassChecker | None = None,
    is_readonly_receiver: bool | None = None,
    is_consuming_receiver: bool | None = None,
    protocol_classifier: ProtocolClassifier | None = None,
    type_ops: 'TypeOperations | None' = None,
    kwarg_types: dict[str, TpyType] | None = None,
) -> FunctionInfo | None:
    """`rank_overload_candidates` over ``overloads`` at one argument list.

    ``protocol_classifier`` enables explicit-vs-structural tier discrimination;
    when omitted, protocol hits are conservatively scored as STRUCTURAL.
    ``type_ops`` enables the generic strict path (inference + tier
    ranking); when omitted, generic overloads fall back to the legacy
    structural match (first-match-wins in declaration order).

    ``kwarg_types`` maps kwarg names to their analyzed types; per-overload
    expansion inserts them at the matching param slots so kwargs can
    disambiguate overloads that positional args tie on.

    Returns the winning ``FunctionInfo`` or ``None`` when none matches;
    a genuine tie at the top raises ``OverloadAmbiguityError`` rather than
    being broken by declaration order.
    """
    # Pre-filter consuming vs borrowing overloads by receiver ownership.
    # Must run BEFORE readonly filter: consuming overloads are non-readonly,
    # so the readonly filter would drop the borrowing (readonly) overload
    # and leave only the consuming one for mutable receivers.
    if is_consuming_receiver is not None and any(m.is_consuming for m in overloads):
        if is_consuming_receiver:
            consuming = [m for m in overloads if m.is_consuming]
            if consuming:
                overloads = consuming
        else:
            borrowing = [m for m in overloads if not m.is_consuming]
            if borrowing:
                overloads = borrowing

    if is_readonly_receiver is not None:
        if is_readonly_receiver:
            ro = [m for m in overloads if m.is_readonly]
            if ro:
                overloads = ro
        else:
            mut = [m for m in overloads if not m.is_readonly]
            if mut:
                overloads = mut

    pick = rank_overload_candidates(
        [OverloadCandidate(ov, arg_types, kwarg_types) for ov in overloads],
        protocol_checker=protocol_checker,
        classifier=protocol_classifier or _bool_checker_to_classifier(protocol_checker),
        deref_checker=deref_checker,
        default_int_type=default_int_type,
        subclass_checker=subclass_checker,
        type_ops=type_ops,
    )
    return overloads[pick.winner] if pick.winner is not None else None
