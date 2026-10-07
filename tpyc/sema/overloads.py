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
    TpyType, IntLiteralType, FloatLiteralType, BIGINT,
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
from ..coercions import resolve_coercion, CoercionContext
from ..identity_map import IdentityMap
from .protocols import ProtocolConformanceKind

if TYPE_CHECKING:
    from .type_ops import TypeOperations
    ProtocolChecker = Callable[[TpyType, TpyType], bool]
    ProtocolClassifier = Callable[[TpyType, TpyType], 'ProtocolConformanceKind | None']
    DerefChecker = Callable[[TpyType], TpyType | None]
    SubclassChecker = Callable[['NominalType', 'NominalType'], bool]


class MatchTier(Enum):
    """Specificity of a strict-pass overload match.

    The integer ``value`` is the rank (lower = more specific) and doubles as
    an index into the per-tier count array in ``_score``. Not an ``IntEnum``
    so that stray int arithmetic or ``tier == 1`` comparisons don't silently
    type-check.
    """
    EXACT_CONCRETE = 1             # arg_inner == param_inner (or nominal-adjacent: IntLit->FixedInt, NoneType->Void, Callable->Fn, ...)
    EXACT_GENERIC_SHAPE = 2        # generic param with concrete outer shape: list[T] vs list[int32]
    PROTOCOL_EXPLICIT = 3          # protocol conformance via classify_protocol_conformance -> EXPLICIT
    PROTOCOL_STRUCTURAL = 4        # protocol conformance via classify_protocol_conformance -> STRUCTURAL
    GENERIC_PROTOCOL_EXPLICIT = 5  # generic over protocol param, explicit conformance
    GENERIC_PROTOCOL_STRUCTURAL = 6  # generic over protocol param, structural conformance
    GENERIC_WILDCARD = 7           # bare T / unconstrained generic


_NUM_MATCH_TIERS = len(MatchTier)


# MatchTier enum is 1-indexed (EXACT_CONCRETE = 1), so score vectors drop slot 0.
_TIER_COUNTS_SIZE = _NUM_MATCH_TIERS + 1


class OverloadAmbiguityError(Exception):
    """Raised by ``resolve_overload`` when multiple candidates tie at the top
    of first-pass ranking (same tier counts, same widening cost, distinct
    signatures).

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
    tier wins); secondary key is total widening cost so same-tier candidates
    prefer the narrowest widening.
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
    if isinstance(arg, NominalType) and arg.name != param.name:
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

    ``IntLiteralType`` ranks as the ``default_int_type`` would (so a list of
    ``int`` literals under ``default_int=int32`` scores ``Iterable[int32]``
    exactly like ``list[int32]`` does). When ``default_int_type`` is None,
    IntLiteral comparisons fall back to the generic cross-type distance.
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
    a_tr = int_traits_of(actual)
    e_tr = int_traits_of(expected)
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
) -> int:
    """Aggregate widening cost between ``actual``'s and ``param``'s type args.

    Caller has already confirmed ``actual`` conforms to ``param``. Walks the
    type-arg tuples pairwise when they line up; for single-element protocols
    also falls back to ``actual.get_element_type()`` so non-parameterised
    containers (``bytes``/``bytearray`` -> ``Iterable[uint8]``) can be scored
    by their baked-in element type. Returns 0 when no type-arg information
    is available.
    """
    if not isinstance(param, NominalType) or not param.type_args:
        return 0
    if isinstance(actual, PendingContainerType):
        # Ranked as the container it spells, so a dict literal is scored
        # as a dict is, never by its value type.
        actual = actual.spelled(actual.parts())
    actual_args: tuple[TpyType, ...] = ()
    if isinstance(actual, NominalType):
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
        if not isinstance(p, TpyType):
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


def _classify_strict_match(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_classifier: ProtocolClassifier | None = None,
    default_int_type: TpyType | None = None,
    type_ops: 'TypeOperations | None' = None,
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
    # IntLiteralType matches the specific fixed-width int it was inferred to (from
    # generic resolution). This allows resolved-generic overloads like
    # range(stop: int32) to match IntLiteralType(5) in the first pass.
    # Cost ranks by widening distance from default_int_type so str(IntLiteralType)
    # picks the default-width overload (int32) over the smallest-fitting one
    # (int8) when multiple fixed-int overloads accept the value.
    if isinstance(arg_inner, IntLiteralType) and is_fixed_int_type(param_inner):
        tr = int_traits_of(param_inner)
        if arg_inner.value is None or tr.min_value <= arg_inner.value <= tr.max_value:
            return (MatchTier.EXACT_CONCRETE,
                    _scalar_widening_cost(arg_inner, param_inner, default_int_type))
    # None literal (NoneType) matches None type annotation (VoidType)
    if isinstance(arg_inner, NoneType) and isinstance(param_inner, VoidType):
        return (MatchTier.EXACT_CONCRETE, 0)
    # T -> Optional[T] and None -> Optional[T]
    if isinstance(param_inner, OptionalType):
        if isinstance(arg_inner, NoneType):
            return (MatchTier.EXACT_CONCRETE, 0)
        return _classify_strict_match(arg_inner, param_inner.inner, protocol_classifier, default_int_type,
                                      type_ops)
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
    # Only in strict pass -- LiteralType -> base type is deferred to coercion pass
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
                    _type_args_widening_cost(check_arg, check_param, default_int_type))
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


def _classify_generic_param_match(
    param_type: TpyType,
    arg_type: TpyType,
    inferred: dict[str, TpyType],
    type_ops: 'TypeOperations',
    protocol_classifier: ProtocolClassifier | None,
    default_int_type: TpyType | None = None,
) -> tuple[MatchTier, int]:
    """Tier + widening cost for a generic-overload param with ``TypeParamRef``.

    Must be called only for params where ``contains_type_param(param_type)``
    is True and ``infer_type_params_for_function`` has already succeeded for the
    whole overload (guaranteeing this param's own inference consistency).

    Shape taxonomy:
    - Bare ``TypeParamRef`` (possibly wrapped in Ref/Readonly) -> GENERIC_WILDCARD.
    - Protocol with ``TypeParamRef`` in type args -> GENERIC_PROTOCOL_EXPLICIT
      or GENERIC_PROTOCOL_STRUCTURAL (fall back to STRUCTURAL if no classifier).
    - Anything else (concrete outer shape: ``list[T]``, ``tuple[T, T]``,
      ``Ptr[T]``, user record with T slot, ...) -> EXACT_GENERIC_SHAPE.

    Cost is 0 except for protocol matches where arg type args widen to the
    substituted protocol type args.
    """
    inspect = param_type
    while isinstance(inspect, (RefType, ReadonlyType)):
        inspect = inspect.wrapped
    if isinstance(inspect, TypeParamRef):
        return (MatchTier.GENERIC_WILDCARD, 0)
    if is_protocol_type(inspect) and contains_type_param(inspect):
        substituted = type_ops.substitute_types(inspect, inferred)
        check_arg = unwrap_ref_type(unwrap_readonly(arg_type))
        if isinstance(check_arg, OwnType):
            check_arg = check_arg.wrapped
        if protocol_classifier is not None and is_protocol_type(substituted):
            kind = protocol_classifier(check_arg, substituted)
            if kind is ProtocolConformanceKind.EXPLICIT:
                return (MatchTier.GENERIC_PROTOCOL_EXPLICIT,
                        _type_args_widening_cost(check_arg, substituted, default_int_type))
            if kind is ProtocolConformanceKind.STRUCTURAL:
                return (MatchTier.GENERIC_PROTOCOL_STRUCTURAL, 0)
        return (MatchTier.GENERIC_PROTOCOL_STRUCTURAL, 0)
    return (MatchTier.EXACT_GENERIC_SHAPE, 0)


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
            if arg_type.value is None:
                return True  # Unknown value -- can't range-check, allow match
            tr = int_traits_of(param_type)
            return tr.min_value <= arg_type.value <= tr.max_value
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

    Used for overload resolution second pass and constructor matching.
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
    # Inheritance: Child -> Parent (value upcast and pointer coercions)
    if subclass_checker:
        if isinstance(arg_inner, NominalType) and arg_inner.is_user_record:
            if isinstance(param_inner, NominalType) and param_inner.is_user_record:
                if subclass_checker(arg_inner, param_inner):
                    return True
            pointee = getattr(param_inner, 'pointee', None)
            if isinstance(pointee, NominalType) and pointee.is_user_record:
                if subclass_checker(arg_inner, pointee):
                    return True
        if isinstance(arg_inner, PtrType) and isinstance(arg_inner.pointee, NominalType):
            if isinstance(param_inner, PtrType) and isinstance(param_inner.pointee, NominalType):
                # Ptr[readonly[T]] cannot coerce to mutable Ptr (would drop const)
                if not (arg_inner.is_readonly and not param_inner.is_readonly):
                    if subclass_checker(arg_inner.pointee, param_inner.pointee):
                        return True
    # Non-protocol NominalType with Own[T] in type args: strip Own for overload matching.
    # Symmetric with the same block in type_matches_strict.
    if (isinstance(param_inner, NominalType) and not is_protocol_type(param_inner)
            and any(isinstance(a, OwnType) for a in param_inner.inner_types())):
        stripped_param = strip_own_type_args(param_inner)
        return arg_inner == stripped_param
    return False


def _int_literal_penalty(arg_t: TpyType, ptype: TpyType,
                         default_int_type: TpyType) -> int:
    """How far an integer literal argument's parameter is from the default
    integer type (0 there), so a literal prefers the default width."""
    if not isinstance(arg_t, IntLiteralType):
        return 0
    ptype = unwrap_ref_type(ptype)
    if ptype == default_int_type:
        return 0
    default_tr = int_traits_of(default_int_type)
    if default_tr is not None:
        ptype_tr = int_traits_of(ptype)
        if ptype_tr is not None:
            # Keep preference stable around configured width/signedness.
            width_gap = abs(ptype_tr.bits - default_tr.bits) // 8
            sign_penalty = 1 if ptype_tr.signed != default_tr.signed else 0
            return 1 + width_gap + sign_penalty
        if is_big_int_type(ptype):
            return 8
    if is_big_int_type(default_int_type):
        if is_fixed_int_type(ptype):
            return 2
    return 1


class CoercionFit(NamedTuple):
    """A candidate's coercion-pass match (`coercion_pass_rank`): its
    container widening (`_container_fit`), how many arguments match without
    a conversion, how far the call narrows a scalar (a BigInt into a
    fixed-width slot, an integer literal away from the default width), and
    whether it is a generic one's instantiation."""
    widening: int
    exact: int
    narrowing: int
    generic: bool

    def rank(self) -> tuple[int, int, int, bool]:
        """Lower is better: the least container widening, then the most
        arguments matching without a conversion, the least narrowing, then
        a concrete signature over a generic instantiation."""
        return (self.widening, -self.exact, self.narrowing, self.generic)


def coercion_pass_rank(
    args: list[TpyType], params: list[TpyType],
    protocol_checker: ProtocolChecker | None,
    deref_checker: DerefChecker | None,
    subclass_checker: SubclassChecker | None,
    type_ops: 'TypeOperations | None',
    default_int_type: TpyType | None,
    declared: FunctionInfo | None = None,
    generic: bool = False,
) -> CoercionFit | None:
    """A candidate's fit in the coercion pass, or None when an argument
    does not convert to its parameter. `declared`: the candidate, asked
    which parameters the call resolves (`call_resolves_param`), so a view
    there decides; `generic`: it is a generic one's instantiation."""
    meets = [(_undecided_container_arm(
                  a, p, type_ops,
                  declared is not None and call_resolves_param(declared, i))
              if _undecided_container(a) else None)
             for i, (a, p) in enumerate(zip(args, params))]
    widening = _container_fit(meets, default_int_type, type_ops)
    if widening is None:
        return None
    if not all(type_matches_with_coercion(a, p, protocol_checker, deref_checker,
                                          subclass_checker)
               for a, p, meet in zip(args, params, meets)
               if meet is None or is_protocol_type(
                   unwrap_ref_type(unwrap_readonly(p)))):
        return None
    exact = sum(1 for a, p in zip(args, params) if type_matches_numeric(a, p))
    default = default_int_type if default_int_type is not None else BIGINT
    narrowing = sum(1 for a, p in zip(args, params)
                    if is_big_int_type(a) and is_fixed_int_type(unwrap_ref_type(p)))
    narrowing += sum(_int_literal_penalty(a, p, default)
                     for a, p in zip(args, params))
    return CoercionFit(widening, exact, narrowing, generic)


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
    """A candidate's strict-pass match (`_classify_overload`): its
    container widening (`_container_fit`) and its per-argument tiers."""
    widening: int
    per_arg: tuple[tuple[MatchTier, int], ...]

    def rank(self) -> tuple[int, tuple[int, ...], int]:
        """Lower is better: the container widening first -- an overload
        never widens a container another applicable one takes as it is --
        then the tiers and the cost (`_score`)."""
        return (self.widening, *_score(self.per_arg))


def _classify_overload(
    overload: FunctionInfo,
    arg_types: list[TpyType],
    protocol_checker: ProtocolChecker | None,
    classifier: ProtocolClassifier | None,
    default_int_type: TpyType | None,
    type_ops: 'TypeOperations | None',
    kwarg_types: dict[str, TpyType] | None = None,
    joined_out: 'IdentityMap[FunctionInfo, dict[str, TpyType]] | None' = None,
) -> OverloadFit | None:
    """Classify every arg against ``overload``'s params, returning its
    container widening and per-arg tier vector on match or ``None`` if any
    arg rejects (or the container arguments do not fit it together). A generic
    overload refused for a joined fixed-int binding leaves its bindings in
    ``joined_out`` for the second pass.

    For generic overloads (``TypeParamRef`` in params), inference runs first
    (using ``protocol_checker`` to validate type-parameter bounds) and
    ``_classify_generic_param_match`` is used for the TPR params; other
    params fall through to the regular strict classifier.
    """
    if kwarg_types:
        expanded = _expand_arg_types_with_kwargs(arg_types, kwarg_types, overload)
        if expanded is None:
            return None
        arg_types = expanded
    if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
        return None
    has_tpr = overload.is_generic() and any(
        contains_type_param(p.type) for p in overload.params)
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
        # strict match; the second pass ranks it with the other conversions.
        if inferred is not None and joined and joined_out is not None:
            joined_out[overload] = inferred
        if inferred is None or joined:
            return None
    per_arg: list[tuple[MatchTier, int]] = []
    meets: list[ContainerMeet | None] = []
    for i, (arg_t, (_, ptype)) in enumerate(zip(arg_types, overload.params)):
        resolved = ptype
        if (has_tpr and _undecided_container(arg_t)
                and contains_type_param(ptype)):
            resolved = type_ops.substitute_types(ptype, inferred)
        meet = (_undecided_container_arm(arg_t, resolved, type_ops,
                                         call_resolves_param(overload, i))
                if _undecided_container(arg_t) else None)
        meets.append(meet)
        if meet is not None and not is_protocol_type(
                unwrap_ref_type(unwrap_readonly(resolved))):
            if not meet.accepted:
                return None
            cell = (MatchTier.EXACT_CONCRETE if resolved is ptype
                    else MatchTier.EXACT_GENERIC_SHAPE, 0)
        elif has_tpr and contains_type_param(ptype):
            cell = _classify_generic_param_match(
                ptype, arg_t, inferred, type_ops, classifier, default_int_type,
            )
        else:
            cell = _classify_strict_match(arg_t, ptype, classifier, default_int_type,
                                          type_ops)
        if cell is None:
            return None
        per_arg.append(cell)
    widening = _container_fit(meets, default_int_type, type_ops)
    if widening is None:
        return None
    return OverloadFit(widening, tuple(per_arg))


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
    """Two-pass overload resolution: strict tier-ranked match, then coercions.

    ``protocol_classifier`` enables explicit-vs-structural tier discrimination;
    when omitted, protocol hits are conservatively scored as STRUCTURAL.
    ``type_ops`` enables the generic first-pass path (inference + tier
    ranking); when omitted, generic overloads fall back to the legacy
    structural match (first-match-wins in declaration order).

    ``kwarg_types`` maps kwarg names to their analyzed types; per-overload
    expansion inserts them at the matching param slots so kwargs can
    disambiguate overloads that positional args tie on.

    Returns the winning ``FunctionInfo`` or ``None`` (no match, or ambiguous
    -- genuine ties at the top of the first-pass score are refused rather
    than broken by declaration order).
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

    classifier = protocol_classifier or _bool_checker_to_classifier(protocol_checker)

    first_generic_match_fallback: FunctionInfo | None = None
    joined_bindings: IdentityMap[FunctionInfo, dict[str, TpyType]] = IdentityMap()
    scored_candidates: list[tuple[tuple[int, tuple[int, ...], int], FunctionInfo]] = []
    for overload in overloads:
        if type_ops is None and overload.is_generic() and any(
                contains_type_param(p.type) for p in overload.params):
            # Legacy structural-match fallback for generic overloads when the
            # caller hasn't plumbed type_ops (e.g. the error-message-preservation
            # path in calls.py). Kwargs aren't supported on this fallback path
            # because it uses raw structural matching without arity expansion.
            if kwarg_types:
                continue
            if (first_generic_match_fallback is None
                    and len(arg_types) >= overload.min_args
                    and len(arg_types) <= overload.max_args
                    and all(_structural_match(arg_t, ptype)
                            for arg_t, (_, ptype) in zip(arg_types, overload.params))):
                first_generic_match_fallback = overload
            continue
        fit = _classify_overload(
            overload, arg_types, protocol_checker, classifier, default_int_type, type_ops,
            kwarg_types=kwarg_types, joined_out=joined_bindings,
        )
        if fit is not None:
            scored_candidates.append((fit.rank(), overload))

    if scored_candidates:
        if len(scored_candidates) == 1:
            return scored_candidates[0][1]
        scored_candidates.sort(key=lambda c: c[0])
        best_score = scored_candidates[0][0]
        # Functionally-identical signatures aren't genuine ambiguity: a
        # substituted generic and its concrete twin (both appended by
        # calls.py's unified pool) match identically. Dedupe by param-type
        # sequence; stable sort keeps the first-declared.
        seen: set[tuple[TpyType, ...]] = set()
        unique_tied: list[FunctionInfo] = []
        for score, ov in scored_candidates:
            if score != best_score:
                break
            key = tuple(p.type for p in ov.params)
            if key not in seen:
                seen.add(key)
                unique_tied.append(ov)
        if len(unique_tied) > 1:
            raise OverloadAmbiguityError(tuple(unique_tied))
        return unique_tied[0]

    if first_generic_match_fallback is not None:
        return first_generic_match_fallback

    # Second pass: allow coercions, prefer overload with most non-coercion
    # matches and fewest narrowing conversions (BigInt->int32 is lossy).
    candidates: list[tuple[CoercionFit, FunctionInfo]] = []
    for overload in overloads:
        effective_args = arg_types
        if kwarg_types:
            expanded = _expand_arg_types_with_kwargs(arg_types, kwarg_types, overload)
            if expanded is None:
                continue
            effective_args = expanded
        if len(effective_args) < overload.min_args or len(effective_args) > overload.max_args:
            continue
        params = [p.type for p in overload.params]
        # A joined fixed-int binding is the generic match the strict pass
        # refused; here it is ranked at its instantiation.
        if type_ops is not None and overload in joined_bindings:
            params = [p.type for p in type_ops.substitute_method_type_params(
                overload, joined_bindings[overload]).params]
        fit = coercion_pass_rank(effective_args, params, protocol_checker,
                                 deref_checker, subclass_checker, type_ops,
                                 default_int_type,
                                 declared=overload,
                                 generic=overload.is_generic())
        if fit is not None:
            candidates.append((fit, overload))

    if candidates:
        candidates.sort(key=lambda c: c[0].rank())
        return candidates[0][1]

    return None
